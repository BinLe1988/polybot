#!/usr/bin/env python3
"""
btc5m_undervalued.py
Polymarket BTC 5m — undervalued_side_observe (OBSERVE-ONLY, never places orders)

Strategy undervalued_side_observe:
  1. Do not trade ~50¢; only consider a side when its ask ∈ [MIN_PRICE, MAX_PRICE]
  2. Only scan when remaining seconds ∈ ENTRY_WINDOW_SEC
  3. Extra stats (no size): ask > STRONG_SIDE_PRICE and rem < STRONG_SIDE_WINDOW_SEC
     → 「结算前强势侧」
  4. OBSERVE=True: record signals; after window ends approximate settlement via
     Binance final vs window open; hypothetical PnL with POSITION_USD
  5. Every STATS_INTERVAL_SEC print: signal count, win rate, payoff ratio, EV, total PnL

Does NOT touch DAXUE live / GUIDED / place_order / .env trading keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import websockets

SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
PID_FILE = LOGS_DIR / "undervalued_observe.pid"

# Proxy for Gamma/CLOB (Clash). Do not load live .env — observe-only, no orders.
for _k, _v in (
    ("HTTPS_PROXY", "http://127.0.0.1:7897"),
    ("HTTP_PROXY", "http://127.0.0.1:7897"),
    ("ALL_PROXY", "http://127.0.0.1:7897"),
):
    os.environ.setdefault(_k, _v)

# --- Strategy constants (hard-coded; OBSERVE-only) ---
MIN_PRICE = 0.25
MAX_PRICE = 0.45
ENTRY_WINDOW_SEC = (40, 120)  # rem seconds inclusive range
STRONG_SIDE_PRICE = 0.70
STRONG_SIDE_WINDOW_SEC = 60
POSITION_USD = 10.0
STATS_INTERVAL_SEC = 300
OBSERVE = True  # NEVER place orders
POLL_SEC = 2.0
HEARTBEAT_SEC = 30.0
STRATEGY = "undervalued_side_observe"

assert OBSERVE is True, "This script is OBSERVE-only; refuse to run with OBSERVE=False"


@dataclass
class PricePoint:
    ts: float
    price: float


@dataclass
class Market:
    token_up: str
    token_down: str
    end_ts: float
    question: str = ""
    slug: str = ""
    start_ts: float = 0.0


@dataclass
class Signal:
    kind: str  # "undervalued" | "strong_side"
    slug: str
    side: str  # UP | DOWN
    ask: float
    rem_sec: float
    spot: float
    open_px: float
    ts: float
    # filled after window settle (undervalued only)
    settled: bool = False
    outcome: str = ""  # UP | DOWN | UNKNOWN
    win: Optional[bool] = None
    pnl: float = 0.0


def setup_logging() -> Tuple[logging.Logger, Path]:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"undervalued_observe_{stamp}.log"
    logger = logging.getLogger("undervalued")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.info(f"[UV] log_file={log_path}")
    return logger, log_path


log, LOG_PATH = setup_logging()


def write_pid() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


class BinanceFeed:
    """BTCUSDT spot via Binance WS (data-stream if main blocked)."""

    def __init__(self):
        self.prices: List[PricePoint] = []
        self.latest = 0.0
        self._task: Optional[asyncio.Task] = None
        self.connected = False
        self.last_error = ""

    async def start(self):
        self._task = asyncio.create_task(self._run())

    def price_at_or_before(self, ts: float) -> Optional[float]:
        """Closest price at or before ts (for window open)."""
        best: Optional[PricePoint] = None
        for p in reversed(self.prices):
            if p.ts <= ts:
                best = p
                break
        if best is None and self.prices:
            # first tick after open — use earliest if within 30s of open
            first = self.prices[0]
            if abs(first.ts - ts) <= 30:
                return first.price
            return None
        if best is None:
            return None
        if abs(best.ts - ts) > 90:
            return None
        return best.price

    def _prune(self, now: float) -> None:
        self.prices = [p for p in self.prices if p.ts > now - 900]

    async def _run(self):
        urls = [
            "wss://data-stream.binance.vision/ws/btcusdt@trade",
            "wss://stream.binance.com:9443/ws/btcusdt@trade",
        ]
        idx = 0
        while True:
            url = urls[idx % len(urls)]
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    self.connected = True
                    self.last_error = ""
                    log.info(f"[UV] Binance WS connected ({url})")
                    async for raw in ws:
                        data = json.loads(raw)
                        price = float(data["p"])
                        now = time.time()
                        self.latest = price
                        self.prices.append(PricePoint(now, price))
                        self._prune(now)
            except Exception as e:
                self.connected = False
                self.last_error = str(e)
                log.error(f"[UV] Binance error ({url}): {e}")
                idx += 1
                await asyncio.sleep(2)


async def create_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False),
        timeout=aiohttp.ClientTimeout(total=12),
        trust_env=True,
    )


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return []
    return list(v) if isinstance(v, (list, tuple)) else []


async def find_btc5m(session: aiohttp.ClientSession) -> Optional[Market]:
    """Discover current BTC 5m Up/Down via gamma slug btc-updown-5m-<windowStart>."""
    now = time.time()
    base = int(now // 300) * 300
    for start in (base - 300, base, base + 300, base + 600):
        slug = f"btc-updown-5m-{start}"
        try:
            async with session.get(
                f"https://gamma-api.polymarket.com/markets/slug/{slug}"
            ) as r:
                if r.status != 200:
                    continue
                m = await r.json()
        except Exception as e:
            log.error(f"[UV] Gamma slug {slug}: {e}")
            continue
        if not isinstance(m, dict) or m.get("closed") is True:
            continue
        end_str = m.get("endDate") or m.get("end_date")
        if not end_str:
            continue
        try:
            end_ts = datetime.fromisoformat(end_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if not (15 < end_ts - now < 320):
            continue
        tokens = _as_list(m.get("clobTokenIds"))
        outcomes = _as_list(m.get("outcomes"))
        if len(tokens) < 2:
            continue
        token_up = token_down = None
        for i, o in enumerate(outcomes):
            ol = str(o).lower()
            if ol == "up" or "up" in ol or "yes" in ol:
                token_up = str(tokens[i])
            elif ol == "down" or "down" in ol or "no" in ol:
                token_down = str(tokens[i])
        if not token_up or not token_down:
            token_up, token_down = str(tokens[0]), str(tokens[1])
        q = (m.get("question") or slug).lower()
        return Market(
            token_up=token_up,
            token_down=token_down,
            end_ts=end_ts,
            question=q,
            slug=slug,
            start_ts=float(start),
        )
    return None


async def get_book(session: aiohttp.ClientSession, token_id: str) -> dict:
    try:
        async with session.get(
            "https://clob.polymarket.com/book", params={"token_id": token_id}
        ) as r:
            return await r.json()
    except Exception:
        return {}


def best_ask(book: dict) -> float:
    asks = book.get("asks") or []
    if not asks:
        return 1.0
    try:
        return min(float(a["price"]) for a in asks)
    except (TypeError, ValueError, KeyError):
        return 1.0


def hyp_pnl(ask: float, win: bool) -> float:
    """Hypothetical PnL for POSITION_USD at ask: win → shares*$1 - cost; lose → -cost."""
    if ask <= 0 or ask >= 1:
        return 0.0
    cost = POSITION_USD
    if win:
        shares = cost / ask
        return shares * 1.0 - cost
    return -cost


class ObserveBot:
    def __init__(self, bn: BinanceFeed):
        self.bn = bn
        self.signals: List[Signal] = []
        # key: (slug, kind, side) — same window + same signal type once
        self._seen: Set[Tuple[str, str, str]] = set()
        # slug -> window open price (Binance)
        self._open_px: Dict[str, float] = {}
        # slug -> end_ts for pending settle
        self._pending_settle: Dict[str, float] = {}
        self._last_stats_ts = time.time()
        self._last_hb_ts = 0.0
        self._last_market_slug = ""
        self._running = True
        self._polls = 0

    def stop(self, *_args):
        self._running = False
        log.info("[UV] stop requested")

    def ensure_open_px(self, market: Market) -> Optional[float]:
        if market.slug in self._open_px and self._open_px[market.slug] > 0:
            return self._open_px[market.slug]
        px = self.bn.price_at_or_before(market.start_ts)
        if px is None and self.bn.latest > 0:
            # fallback: if we just entered window, use latest as proxy open
            if abs(time.time() - market.start_ts) < 45:
                px = self.bn.latest
        if px and px > 0:
            self._open_px[market.slug] = px
            return px
        return None

    def record(
        self,
        kind: str,
        market: Market,
        side: str,
        ask: float,
        rem: float,
        open_px: float,
    ) -> Optional[Signal]:
        key = (market.slug, kind, side)
        if key in self._seen:
            return None
        self._seen.add(key)
        sig = Signal(
            kind=kind,
            slug=market.slug,
            side=side,
            ask=ask,
            rem_sec=rem,
            spot=self.bn.latest,
            open_px=open_px,
            ts=time.time(),
        )
        self.signals.append(sig)
        self._pending_settle[market.slug] = market.end_ts
        return sig

    def settle_due(self) -> None:
        now = time.time()
        done_slugs = []
        for slug, end_ts in list(self._pending_settle.items()):
            if now < end_ts + 2:  # small buffer after window end
                continue
            open_px = self._open_px.get(slug, 0.0)
            # final ≈ last Binance tick at/near end
            final = self.bn.price_at_or_before(end_ts + 1.0)
            if final is None:
                final = self.bn.latest
            if open_px <= 0 or final <= 0:
                outcome = "UNKNOWN"
            elif final > open_px:
                outcome = "UP"
            elif final < open_px:
                outcome = "DOWN"
            else:
                outcome = "UNKNOWN"  # flat — treat as no edge / skip win flag

            for sig in self.signals:
                if sig.slug != slug or sig.settled:
                    continue
                sig.outcome = outcome
                sig.settled = True
                if sig.kind != "undervalued":
                    # strong_side: stats only, no PnL
                    continue
                if outcome == "UNKNOWN":
                    sig.win = None
                    sig.pnl = 0.0
                    continue
                sig.win = sig.side == outcome
                sig.pnl = hyp_pnl(sig.ask, bool(sig.win))
                log.info(
                    f"[UV][SETTLE] {sig.kind} {sig.side} ask={sig.ask:.3f} "
                    f"open={open_px:.2f} final={final:.2f} outcome={outcome} "
                    f"win={sig.win} pnl={sig.pnl:+.3f} slug={slug}"
                )
            done_slugs.append(slug)

        for s in done_slugs:
            self._pending_settle.pop(s, None)

    def print_stats(self, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_stats_ts) < STATS_INTERVAL_SEC:
            return
        self._last_stats_ts = now

        uv = [s for s in self.signals if s.kind == "undervalued"]
        settled = [s for s in uv if s.settled and s.win is not None]
        wins = [s for s in settled if s.win]
        losses = [s for s in settled if not s.win]
        n = len(settled)
        win_rate = (len(wins) / n) if n else 0.0
        avg_win = (sum(s.pnl for s in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(s.pnl for s in losses) / len(losses)) if losses else 0.0
        # payoff ratio = |avg_win / avg_loss|
        if avg_loss != 0:
            payoff = abs(avg_win / avg_loss)
        elif avg_win > 0:
            payoff = float("inf")
        else:
            payoff = 0.0
        total_pnl = sum(s.pnl for s in settled)
        # EV per settled signal
        ev = (total_pnl / n) if n else 0.0
        strong_n = sum(1 for s in self.signals if s.kind == "strong_side")
        pending = sum(1 for s in uv if not s.settled)

        payoff_s = "inf" if math.isinf(payoff) else f"{payoff:.3f}"
        log.info(
            f"[UV][STATS] strategy={STRATEGY} OBSERVE={OBSERVE} "
            f"uv_signals={len(uv)} settled={n} pending={pending} "
            f"wins={len(wins)} losses={len(losses)} "
            f"win_rate={win_rate:.1%} payoff_ratio={payoff_s} "
            f"EV={ev:+.4f} total_PnL={total_pnl:+.3f} "
            f"strong_side_signals={strong_n} POSITION_USD={POSITION_USD}"
        )

    async def poll_once(self, session: aiohttp.ClientSession) -> None:
        self._polls += 1
        self.settle_due()
        self.print_stats()

        market = await find_btc5m(session)
        if not market:
            if time.time() - self._last_hb_ts >= HEARTBEAT_SEC:
                self._last_hb_ts = time.time()
                log.info(
                    f"[UV][HB] no market bn={self.bn.latest:.2f} "
                    f"ws={self.bn.connected} polls={self._polls}"
                )
            return

        if market.slug != self._last_market_slug:
            self._last_market_slug = market.slug
            log.info(
                f"[UV] market={market.slug} end_in={market.end_ts - time.time():.0f}s "
                f"q={market.question[:60]}"
            )

        rem = market.end_ts - time.time()
        open_px = self.ensure_open_px(market)
        spot = self.bn.latest

        if time.time() - self._last_hb_ts >= HEARTBEAT_SEC:
            self._last_hb_ts = time.time()
            log.info(
                f"[UV][HB] slug={market.slug} rem={rem:.0f}s "
                f"spot={spot:.2f} open={open_px or 0:.2f} "
                f"ws={self.bn.connected} signals={len(self.signals)}"
            )

        if rem <= 0:
            return
        if open_px is None or spot <= 0:
            return

        # Fetch books
        book_up, book_dn = await asyncio.gather(
            get_book(session, market.token_up),
            get_book(session, market.token_down),
        )
        ask_up = best_ask(book_up)
        ask_dn = best_ask(book_dn)

        lo, hi = ENTRY_WINDOW_SEC
        in_entry = lo <= rem <= hi

        # 1) Undervalued side in entry window
        if in_entry:
            for side, ask in (("UP", ask_up), ("DOWN", ask_dn)):
                if MIN_PRICE <= ask <= MAX_PRICE:
                    sig = self.record(
                        "undervalued", market, side, ask, rem, open_px
                    )
                    if sig:
                        log.info(
                            f"[UV][SIGNAL] undervalued {side} ask={ask:.3f} "
                            f"rem={rem:.0f}s spot={spot:.2f} open={open_px:.2f} "
                            f"slug={market.slug} OBSERVE={OBSERVE} "
                            f"(no order — hyp_cost=${POSITION_USD})"
                        )

        # 2) Strong side before settle (stats only, no size)
        if rem < STRONG_SIDE_WINDOW_SEC:
            for side, ask in (("UP", ask_up), ("DOWN", ask_dn)):
                if ask > STRONG_SIDE_PRICE:
                    sig = self.record(
                        "strong_side", market, side, ask, rem, open_px
                    )
                    if sig:
                        log.info(
                            f"[UV][SIGNAL] 结算前强势侧 {side} ask={ask:.3f} "
                            f"rem={rem:.0f}s spot={spot:.2f} open={open_px:.2f} "
                            f"slug={market.slug} (stats only, no size)"
                        )

    async def run(self) -> None:
        write_pid()
        log.info(
            f"[UV] START strategy={STRATEGY} OBSERVE={OBSERVE} "
            f"MIN_PRICE={MIN_PRICE} MAX_PRICE={MAX_PRICE} "
            f"ENTRY_WINDOW_SEC={ENTRY_WINDOW_SEC} "
            f"STRONG_SIDE_PRICE={STRONG_SIDE_PRICE} "
            f"STRONG_SIDE_WINDOW_SEC={STRONG_SIDE_WINDOW_SEC} "
            f"POSITION_USD={POSITION_USD} STATS_INTERVAL_SEC={STATS_INTERVAL_SEC} "
            f"pid={os.getpid()} NO_ORDERS=1"
        )
        await self.bn.start()
        # wait briefly for first tick
        for _ in range(40):
            if self.bn.latest > 0:
                break
            await asyncio.sleep(0.25)
        if self.bn.latest <= 0:
            log.warning(
                f"[UV] Binance not ready yet err={self.bn.last_error!r} — continuing"
            )

        async with await create_session() as session:
            while self._running:
                try:
                    await self.poll_once(session)
                except Exception as e:
                    log.error(f"[UV] poll error: {e}")
                await asyncio.sleep(POLL_SEC)

        self.settle_due()
        self.print_stats(force=True)
        log.info("[UV] EXIT")


async def main():
    bn = BinanceFeed()
    bot = ObserveBot(bn)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_: bot.stop())
    await bot.run()


if __name__ == "__main__":
    # Hard guard: never import/call place_order
    asyncio.run(main())
