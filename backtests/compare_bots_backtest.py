#!/usr/bin/env python3
"""Paper backtest: polybot complete-set vs CloddsBot strategies on BTC 5m Up/Down.

NO LIVE ORDERS. Public market data only.
Reuses patterns from run_backtest.py; does not modify that script.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Prefer local proxy only when usable (Mac mini); box often has no :7890.
def _proxy_ok(url: str = "http://127.0.0.1:7890") -> bool:
    import socket
    try:
        host, port = "127.0.0.1", 7890
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False

if _proxy_ok():
    for k, v in {
        "HTTPS_PROXY": "http://127.0.0.1:7890",
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "ALL_PROXY": "http://127.0.0.1:7890",
    }.items():
        os.environ.setdefault(k, v)
else:
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(k, None)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "compare"
CACHE_DIR = OUT_DIR / "cache"
MAX_NOTIONAL = 10.0
FEE_RATE = 0.07
TARGET_MARKETS = 200
ENTRY_DELAY_SEC = 15
HALF_SPREAD = 0.005  # half-spread ask/bid approx from mid
ASK_TICK = 0.01
SH_TZ = timezone(timedelta(hours=8))

# polybot min edges
POLYBOT_EDGES = [0.001, 0.01]  # develop.yaml vs research default

# CloddsBot defaults from src/trading/bots/index.ts + /arbitrage minEdge=1%
CLODDS_MR_LOOKBACK = 20
CLODDS_MR_ENTRY_Z = 2.0
CLODDS_MR_EXIT_Z = 0.5
CLODDS_MR_STOP_PCT = 10.0
CLODDS_MR_TP_PCT = 20.0
CLODDS_MOM_SHORT = 5
CLODDS_MOM_LONG = 20
CLODDS_MOM_MIN = 0.05
CLODDS_MOM_STOP_PCT = 15.0
CLODDS_MOM_TP_PCT = 30.0
CLODDS_ARB_MIN_EDGE = 0.01  # 1% absolute (ask_sum < 1 - 0.01)


def http_get(url: str, timeout: float = 30.0, retries: int = 4) -> Any:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "poly-test-compare-bots/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(0.4 * (2 ** attempt))
    raise RuntimeError(f"GET failed {url}: {last_err}")


def taker_fee(shares: float, price: float) -> float:
    p = max(0.0, min(1.0, float(price)))
    return float(shares) * FEE_RATE * p * (1.0 - p)


def max_drawdown(equity_curve: List[float]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for x in equity_curve:
        peak = max(peak, x)
        dd = peak - x
        if dd > max_dd:
            max_dd = dd
    return max_dd


def ts_to_sh(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=SH_TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")


def parse_tokens(m: dict) -> Tuple[str, str]:
    raw = m.get("clobTokenIds")
    tokens = json.loads(raw) if isinstance(raw, str) else raw
    return tokens[0], tokens[1]


def parse_outcomes(m: dict) -> Tuple[List[str], List[float]]:
    outs = m.get("outcomes")
    prices = m.get("outcomePrices")
    outs = json.loads(outs) if isinstance(outs, str) else outs
    prices = json.loads(prices) if isinstance(prices, str) else prices
    return outs, [float(p) for p in prices]


def resolve_winner(outs: List[str], prices: List[float]) -> Tuple[str, float, float]:
    up_i = outs.index("Up") if "Up" in outs else 0
    down_i = outs.index("Down") if "Down" in outs else 1
    up_p, down_p = prices[up_i], prices[down_i]
    if up_p >= 0.99 and down_p <= 0.01:
        return "Up", up_p, down_p
    if down_p >= 0.99 and up_p <= 0.01:
        return "Down", up_p, down_p
    if up_p > down_p:
        return "Up", up_p, down_p
    if down_p > up_p:
        return "Down", up_p, down_p
    return "unknown", up_p, down_p


def fetch_price_history(token: str, start_ts: int, end_ts: int) -> List[dict]:
    q = urllib.parse.urlencode(
        {"market": token, "startTs": start_ts, "endTs": end_ts, "fidelity": 1}
    )
    data = http_get(f"https://clob.polymarket.com/prices-history?{q}")
    hist = data.get("history") if isinstance(data, dict) else data
    return hist or []


def buy_shares_for_notional(price: float, notional: float) -> Tuple[float, float, float]:
    p = max(0.01, min(0.99, float(price)))
    denom = 1.0 + FEE_RATE * (1.0 - p)
    spend = notional / denom
    shares = spend / p
    fee = taker_fee(shares, p)
    return shares, spend, fee


def nearest_mid(hist: List[dict], target: int, window_ts: int) -> Optional[float]:
    if not hist:
        return None
    after = [h for h in hist if h["t"] >= window_ts]
    pool = after if after else hist
    best = min(pool, key=lambda h: abs(h["t"] - target))
    return float(best["p"])


def align_series(
    up_hist: List[dict], down_hist: List[dict], window_ts: int
) -> List[dict]:
    """Aligned (t, up_mid, down_mid) within [window_ts, window_ts+300]."""
    if not up_hist or not down_hist:
        return []
    down_sorted = sorted(down_hist, key=lambda h: h["t"])
    downs_t = [h["t"] for h in down_sorted]
    downs_p = [float(h["p"]) for h in down_sorted]

    def nearest_down(t: int) -> Optional[float]:
        lo, hi = 0, len(downs_t) - 1
        best_i, best_d = 0, abs(downs_t[0] - t)
        while lo <= hi:
            mid = (lo + hi) // 2
            d = abs(downs_t[mid] - t)
            if d < best_d:
                best_d, best_i = d, mid
            if downs_t[mid] < t:
                lo = mid + 1
            else:
                hi = mid - 1
        if best_d > 30:
            return None
        return downs_p[best_i]

    out = []
    for h in sorted(up_hist, key=lambda x: x["t"]):
        t = int(h["t"])
        if t < window_ts or t > window_ts + 300:
            continue
        dn = nearest_down(t)
        if dn is None:
            continue
        up_p = float(h["p"])
        out.append({"t": t, "up": up_p, "down": dn})
    return out


@dataclass
class MarketData:
    slug: str
    title: str
    window_ts: int
    volume: float
    up_token: str
    down_token: str
    winner: str
    up_final: float
    down_final: float
    series: List[dict] = field(default_factory=list)  # aligned mids
    skip_reason: str = ""

    @property
    def entry_up(self) -> Optional[float]:
        if not self.series:
            return None
        target = self.window_ts + ENTRY_DELAY_SEC
        return min(self.series, key=lambda p: abs(p["t"] - target))["up"]

    @property
    def entry_down(self) -> Optional[float]:
        if not self.series:
            return None
        target = self.window_ts + ENTRY_DELAY_SEC
        return min(self.series, key=lambda p: abs(p["t"] - target))["down"]


def collect_or_load(n_target: int, force: bool = False) -> List[MarketData]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"markets_{n_target}.json"
    if cache_path.exists() and not force:
        print(f"Loading cache {cache_path}", flush=True)
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        return [MarketData(**m) for m in raw]

    now = int(time.time())
    win = now - (now % 300)
    rows: List[MarketData] = []
    i = 2
    empty_streak = 0
    print(f"Collecting up to {n_target} closed markets from win={win} ...", flush=True)

    while len(rows) < n_target and i < n_target * 4 + 50:
        ts = win - i * 300
        i += 1
        try:
            data = http_get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{ts}")
        except Exception as e:
            print(f"  warn fetch slug {ts}: {e}", flush=True)
            empty_streak += 1
            if empty_streak > 30:
                break
            continue
        if not data:
            empty_streak += 1
            if empty_streak > 40:
                break
            continue
        empty_streak = 0
        e = data[0]
        if not e.get("closed"):
            continue
        m = e["markets"][0]
        outs, prices = parse_outcomes(m)
        winner, up_f, dn_f = resolve_winner(outs, prices)
        if winner == "unknown":
            continue
        up_tok, dn_tok = parse_tokens(m)
        try:
            up_hist = fetch_price_history(up_tok, ts - 120, ts + 360)
            time.sleep(0.04)
            dn_hist = fetch_price_history(dn_tok, ts - 120, ts + 360)
            time.sleep(0.04)
        except Exception as ex:
            print(f"  warn prices {ts}: {ex}", flush=True)
            up_hist, dn_hist = [], []

        series = align_series(up_hist, dn_hist, ts)
        skip = "" if series else "no_price_series"
        row = MarketData(
            slug=e.get("slug") or m.get("slug"),
            title=e.get("title") or m.get("question") or "",
            window_ts=ts,
            volume=float(e.get("volume") or m.get("volume") or 0),
            up_token=up_tok,
            down_token=dn_tok,
            winner=winner,
            up_final=up_f,
            down_final=dn_f,
            series=series,
            skip_reason=skip,
        )
        rows.append(row)
        if len(rows) % 25 == 0:
            print(f"  collected {len(rows)} ... last={row.slug} pts={len(series)}", flush=True)

    print(f"Done collecting: {len(rows)} markets", flush=True)
    cache_path.write_text(
        json.dumps([asdict(r) for r in rows], indent=None), encoding="utf-8"
    )
    print(f"Wrote cache {cache_path}", flush=True)
    return rows


@dataclass
class Trade:
    slug: str
    strategy: str
    family: str
    side: str  # Up / Down / BOTH
    entry_t: int
    entry_up: float
    entry_down: float
    fill_mode: str
    shares: float
    notional: float
    fee: float
    edge_gross: float
    winner: str
    won: Optional[bool]  # None for pair arb (always redeem)
    pnl_gross: float
    pnl_net: float
    exit_reason: str
    skip_note: str = ""


def summarize(name: str, family: str, trades: List[Trade], n_markets: int, skips: Dict[str, int]) -> dict:
    if not trades:
        return {
            "strategy": name,
            "family": family,
            "trades": 0,
            "markets_traded": 0,
            "win_rate": None,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "total_fees": 0.0,
            "max_drawdown": 0.0,
            "avg_edge_gross": None,
            "avg_pnl_net": None,
            "total_notional": 0.0,
            "n_markets": n_markets,
            "skip_reasons": skips,
        }
    decided = [t for t in trades if t.won is not None]
    wins = sum(1 for t in decided if t.won)
    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.pnl_net)
    edges = [t.edge_gross for t in trades if t.edge_gross is not None]
    markets = len({t.slug for t in trades})
    return {
        "strategy": name,
        "family": family,
        "trades": len(trades),
        "markets_traded": markets,
        "wins": wins if decided else None,
        "win_rate": (wins / len(decided)) if decided else None,
        "net_pnl": sum(t.pnl_net for t in trades),
        "gross_pnl": sum(t.pnl_gross for t in trades),
        "total_fees": sum(t.fee for t in trades),
        "max_drawdown": max_drawdown(equity),
        "avg_edge_gross": (sum(edges) / len(edges)) if edges else None,
        "avg_pnl_net": sum(t.pnl_net for t in trades) / len(trades),
        "total_notional": sum(t.notional for t in trades),
        "n_markets": n_markets,
        "skip_reasons": skips,
    }


# ---------------------------------------------------------------------------
# polybot: complete-set MM / Gabagool replica adapted to 5m
# Core: when 1 - (bid_UP + bid_DOWN) >= min_edge, buy both legs, hold to redeem $1
# Without L2: bid≈mid-HALF_SPREAD (maker-optimistic) or ask≈mid+HALF_SPREAD (conservative)
# ---------------------------------------------------------------------------

def run_polybot_complete_set(
    markets: List[MarketData], min_edge: float, gate: str
) -> Tuple[List[Trade], Dict[str, int]]:
    """
    gate:
      mid_optimistic — use mid as proxy for bid (optimistic; labels mid-sum gate)
      bid_approx — bid = mid - HALF_SPREAD (maker-style at bids)
      ask_conservative — ask = mid + HALF_SPREAD; enter when 1-(ask_u+ask_d)>=min_edge
    One entry per market at first qualifying tick after ENTRY_DELAY; equal shares; hold to resolution.
    """
    trades: List[Trade] = []
    skips: Dict[str, int] = {
        "no_series": 0,
        "no_edge_in_window": 0,
        "entered": 0,
    }
    name = f"polybot_cs_edge{min_edge}_{gate}"

    for m in markets:
        if not m.series:
            skips["no_series"] += 1
            continue
        entered = False
        for pt in m.series:
            if pt["t"] < m.window_ts + ENTRY_DELAY_SEC:
                continue
            up_m, dn_m = pt["up"], pt["down"]
            if gate == "mid_optimistic":
                pu, pd = up_m, dn_m
                fill_mode = "mid_as_fill"
                edge = 1.0 - (pu + pd)
            elif gate == "bid_approx":
                pu = max(0.01, up_m - HALF_SPREAD)
                pd = max(0.01, dn_m - HALF_SPREAD)
                fill_mode = "bid_mid_minus_hs"
                edge = 1.0 - (pu + pd)  # maker quote at bids
            else:  # ask_conservative
                pu = min(0.99, up_m + HALF_SPREAD)
                pd = min(0.99, dn_m + HALF_SPREAD)
                fill_mode = "ask_mid_plus_hs"
                edge = 1.0 - (pu + pd)

            if edge < min_edge:
                continue

            # Equal shares complete set; total notional capped at MAX_NOTIONAL
            s = pu + pd
            if s <= 0 or s >= 1.0:
                continue
            C = MAX_NOTIONAL / s
            spend = C * s
            # Maker-style on bid_approx: fee=0 optimistic; otherwise taker fee both legs
            if gate == "bid_approx":
                fee = 0.0  # maker at bid assumption (no taker fee)
            else:
                fee = taker_fee(C, pu) + taker_fee(C, pd)
            payout = C  # redeem pair for $1 each share pair
            pnl_g = payout - spend
            pnl_n = payout - spend - fee
            trades.append(
                Trade(
                    slug=m.slug,
                    strategy=name,
                    family="polybot",
                    side="BOTH",
                    entry_t=pt["t"],
                    entry_up=pu,
                    entry_down=pd,
                    fill_mode=fill_mode,
                    shares=C,
                    notional=spend,
                    fee=fee,
                    edge_gross=edge,
                    winner=m.winner,
                    won=None,
                    pnl_gross=pnl_g,
                    pnl_net=pnl_n,
                    exit_reason="hold_to_resolution_redeem",
                )
            )
            skips["entered"] += 1
            entered = True
            break
        if not entered:
            skips["no_edge_in_window"] += 1

    return trades, skips


# ---------------------------------------------------------------------------
# CloddsBot: internal Up+Down arb (from /arbitrage command, minEdge=1%)
# ---------------------------------------------------------------------------

def run_clodds_arb(markets: List[MarketData], min_edge: float = CLODDS_ARB_MIN_EDGE) -> Tuple[List[Trade], Dict[str, int]]:
    trades: List[Trade] = []
    skips: Dict[str, int] = {"no_series": 0, "no_edge": 0, "entered": 0}
    name = f"clodds_updown_arb_edge{min_edge}"

    for m in markets:
        if not m.series:
            skips["no_series"] += 1
            continue
        entered = False
        for pt in m.series:
            if pt["t"] < m.window_ts + ENTRY_DELAY_SEC:
                continue
            # Conservative: use ask approx (mid + half-spread) like live buy both
            pu = min(0.99, pt["up"] + HALF_SPREAD)
            pd = min(0.99, pt["down"] + HALF_SPREAD)
            s = pu + pd
            edge = 1.0 - s
            if edge < min_edge:
                continue
            C = MAX_NOTIONAL / s
            spend = C * s
            fee = taker_fee(C, pu) + taker_fee(C, pd)
            payout = C
            pnl_g = payout - spend
            pnl_n = payout - spend - fee
            trades.append(
                Trade(
                    slug=m.slug,
                    strategy=name,
                    family="cloddsbot",
                    side="BOTH",
                    entry_t=pt["t"],
                    entry_up=pu,
                    entry_down=pd,
                    fill_mode="ask_mid_plus_hs",
                    shares=C,
                    notional=spend,
                    fee=fee,
                    edge_gross=edge,
                    winner=m.winner,
                    won=None,
                    pnl_gross=pnl_g,
                    pnl_net=pnl_n,
                    exit_reason="hold_to_resolution_redeem",
                )
            )
            skips["entered"] += 1
            entered = True
            break
        if not entered:
            skips["no_edge"] += 1
    return trades, skips


# ---------------------------------------------------------------------------
# CloddsBot mean-reversion & momentum
# Source uses lookback 20 at ~1min interval — a single 5m window has too few points.
# Adaptation (documented): rolling Up/Down mid series across consecutive markets
# chronologically; enter at most one position per market; hold to resolution or
# exit mid-window if stop/TP/signal fires on later ticks of same market.
# ---------------------------------------------------------------------------

def _build_global_ticks(markets: List[MarketData]) -> List[dict]:
    """Chronological ticks tagged with slug/winner for directional strategies."""
    ordered = sorted(markets, key=lambda m: m.window_ts)
    ticks = []
    for m in ordered:
        for pt in m.series:
            ticks.append({
                "t": pt["t"],
                "up": pt["up"],
                "down": pt["down"],
                "slug": m.slug,
                "winner": m.winner,
                "window_ts": m.window_ts,
            })
    ticks.sort(key=lambda x: x["t"])
    return ticks


def run_clodds_mean_reversion(markets: List[MarketData]) -> Tuple[List[Trade], Dict[str, int]]:
    """Port of createMeanReversionStrategy: lookback 20, entry z < -2."""
    name = "clodds_mean_reversion"
    trades: List[Trade] = []
    skips: Dict[str, int] = {
        "warmup": 0,
        "no_signal": 0,
        "entered": 0,
        "std_zero": 0,
    }
    ticks = _build_global_ticks(markets)
    hist_up: List[float] = []
    # Open position state
    pos: Optional[dict] = None
    traded_slugs = set()

    for tk in ticks:
        hist_up.append(tk["up"])
        if len(hist_up) > CLODDS_MR_LOOKBACK:
            hist_up.pop(0)
        if len(hist_up) < CLODDS_MR_LOOKBACK:
            skips["warmup"] += 1
            continue

        mean = sum(hist_up) / len(hist_up)
        var = sum((x - mean) ** 2 for x in hist_up) / len(hist_up)
        std = math.sqrt(var)
        if std == 0:
            skips["std_zero"] += 1
            continue
        z = (tk["up"] - mean) / std

        # Manage open position (same market only for mid-window exits)
        if pos is not None:
            if tk["slug"] != pos["slug"]:
                # Market ended without exit — settle at resolution
                won = pos["winner"] == "Up"
                payout = pos["shares"] if won else 0.0
                pnl_g = payout - pos["spend"]
                pnl_n = payout - pos["spend"] - pos["fee"]
                trades.append(
                    Trade(
                        slug=pos["slug"],
                        strategy=name,
                        family="cloddsbot",
                        side="Up",
                        entry_t=pos["entry_t"],
                        entry_up=pos["entry_px"],
                        entry_down=0.0,
                        fill_mode="mid_taker",
                        shares=pos["shares"],
                        notional=pos["spend"],
                        fee=pos["fee"],
                        edge_gross=abs(pos.get("z", 0)) / 10.0,  # soft edge proxy
                        winner=pos["winner"],
                        won=won,
                        pnl_gross=pnl_g,
                        pnl_net=pnl_n,
                        exit_reason="resolution",
                    )
                )
                pos = None
            else:
                pnl_pct = ((tk["up"] - pos["entry_px"]) / pos["entry_px"]) * 100
                exit_reason = None
                if pnl_pct <= -CLODDS_MR_STOP_PCT:
                    exit_reason = f"stop_loss_{pnl_pct:.1f}%"
                elif pnl_pct >= CLODDS_MR_TP_PCT:
                    exit_reason = f"take_profit_{pnl_pct:.1f}%"
                elif z > CLODDS_MR_EXIT_Z:
                    exit_reason = f"z_exit_{z:.2f}"
                if exit_reason:
                    # Sell at mid (taker fee on exit)
                    exit_px = max(0.01, min(0.99, tk["up"]))
                    proceeds = pos["shares"] * exit_px
                    fee_exit = taker_fee(pos["shares"], exit_px)
                    pnl_g = proceeds - pos["spend"]
                    pnl_n = proceeds - pos["spend"] - pos["fee"] - fee_exit
                    # "won" if net positive for directional early exit
                    trades.append(
                        Trade(
                            slug=pos["slug"],
                            strategy=name,
                            family="cloddsbot",
                            side="Up",
                            entry_t=pos["entry_t"],
                            entry_up=pos["entry_px"],
                            entry_down=0.0,
                            fill_mode="mid_taker",
                            shares=pos["shares"],
                            notional=pos["spend"],
                            fee=pos["fee"] + fee_exit,
                            edge_gross=abs(pos.get("z", 0)) / 10.0,
                            winner=pos["winner"],
                            won=pnl_n > 0,
                            pnl_gross=pnl_g,
                            pnl_net=pnl_n,
                            exit_reason=exit_reason,
                        )
                    )
                    pos = None
                continue

        # Entry: z < -entryThreshold, no position, one trade per market
        if pos is None and tk["slug"] not in traded_slugs:
            if tk["t"] < tk["window_ts"] + ENTRY_DELAY_SEC:
                continue
            if z < -CLODDS_MR_ENTRY_Z:
                px = max(0.01, min(0.99, tk["up"]))
                # ask fill conservative
                fill = min(0.99, px + HALF_SPREAD)
                sh, sp, fee = buy_shares_for_notional(fill, MAX_NOTIONAL)
                pos = {
                    "slug": tk["slug"],
                    "entry_t": tk["t"],
                    "entry_px": fill,
                    "shares": sh,
                    "spend": sp,
                    "fee": fee,
                    "winner": tk["winner"],
                    "z": z,
                }
                traded_slugs.add(tk["slug"])
                skips["entered"] += 1

    # Flush open pos
    if pos is not None:
        won = pos["winner"] == "Up"
        payout = pos["shares"] if won else 0.0
        pnl_g = payout - pos["spend"]
        pnl_n = payout - pos["spend"] - pos["fee"]
        trades.append(
            Trade(
                slug=pos["slug"],
                strategy=name,
                family="cloddsbot",
                side="Up",
                entry_t=pos["entry_t"],
                entry_up=pos["entry_px"],
                entry_down=0.0,
                fill_mode="mid_taker",
                shares=pos["shares"],
                notional=pos["spend"],
                fee=pos["fee"],
                edge_gross=abs(pos.get("z", 0)) / 10.0,
                winner=pos["winner"],
                won=won,
                pnl_gross=pnl_g,
                pnl_net=pnl_n,
                exit_reason="resolution",
            )
        )

    if skips["entered"] == 0:
        skips["no_signal"] = len(markets)
    return trades, skips


def run_clodds_momentum(markets: List[MarketData]) -> Tuple[List[Trade], Dict[str, int]]:
    """Port of createMomentumStrategy: short 5 / long 20, minMomentum 0.05."""
    name = "clodds_momentum"
    trades: List[Trade] = []
    skips: Dict[str, int] = {"warmup": 0, "no_signal": 0, "entered": 0}
    ticks = _build_global_ticks(markets)
    hist_up: List[float] = []
    pos: Optional[dict] = None
    traded_slugs = set()

    for tk in ticks:
        hist_up.append(tk["up"])
        if len(hist_up) > CLODDS_MOM_LONG:
            hist_up.pop(0)
        if len(hist_up) < CLODDS_MOM_LONG:
            skips["warmup"] += 1
            continue

        short_ma = sum(hist_up[-CLODDS_MOM_SHORT:]) / CLODDS_MOM_SHORT
        long_ma = sum(hist_up) / len(hist_up)
        if long_ma == 0:
            continue
        momentum = (short_ma - long_ma) / long_ma

        if pos is not None:
            if tk["slug"] != pos["slug"]:
                won = pos["winner"] == "Up"
                payout = pos["shares"] if won else 0.0
                pnl_g = payout - pos["spend"]
                pnl_n = payout - pos["spend"] - pos["fee"]
                trades.append(
                    Trade(
                        slug=pos["slug"],
                        strategy=name,
                        family="cloddsbot",
                        side="Up",
                        entry_t=pos["entry_t"],
                        entry_up=pos["entry_px"],
                        entry_down=0.0,
                        fill_mode="mid_taker",
                        shares=pos["shares"],
                        notional=pos["spend"],
                        fee=pos["fee"],
                        edge_gross=pos.get("mom", 0),
                        winner=pos["winner"],
                        won=won,
                        pnl_gross=pnl_g,
                        pnl_net=pnl_n,
                        exit_reason="resolution",
                    )
                )
                pos = None
            else:
                pnl_pct = ((tk["up"] - pos["entry_px"]) / pos["entry_px"]) * 100
                exit_reason = None
                if pnl_pct <= -CLODDS_MOM_STOP_PCT:
                    exit_reason = f"stop_loss_{pnl_pct:.1f}%"
                elif pnl_pct >= CLODDS_MOM_TP_PCT:
                    exit_reason = f"take_profit_{pnl_pct:.1f}%"
                elif momentum < -CLODDS_MOM_MIN:
                    exit_reason = f"mom_reversal_{momentum*100:.1f}%"
                if exit_reason:
                    exit_px = max(0.01, min(0.99, tk["up"]))
                    proceeds = pos["shares"] * exit_px
                    fee_exit = taker_fee(pos["shares"], exit_px)
                    pnl_g = proceeds - pos["spend"]
                    pnl_n = proceeds - pos["spend"] - pos["fee"] - fee_exit
                    trades.append(
                        Trade(
                            slug=pos["slug"],
                            strategy=name,
                            family="cloddsbot",
                            side="Up",
                            entry_t=pos["entry_t"],
                            entry_up=pos["entry_px"],
                            entry_down=0.0,
                            fill_mode="mid_taker",
                            shares=pos["shares"],
                            notional=pos["spend"],
                            fee=pos["fee"] + fee_exit,
                            edge_gross=pos.get("mom", 0),
                            winner=pos["winner"],
                            won=pnl_n > 0,
                            pnl_gross=pnl_g,
                            pnl_net=pnl_n,
                            exit_reason=exit_reason,
                        )
                    )
                    pos = None
                continue

        if pos is None and tk["slug"] not in traded_slugs:
            if tk["t"] < tk["window_ts"] + ENTRY_DELAY_SEC:
                continue
            if momentum > CLODDS_MOM_MIN:
                px = max(0.01, min(0.99, tk["up"]))
                fill = min(0.99, px + HALF_SPREAD)
                sh, sp, fee = buy_shares_for_notional(fill, MAX_NOTIONAL)
                pos = {
                    "slug": tk["slug"],
                    "entry_t": tk["t"],
                    "entry_px": fill,
                    "shares": sh,
                    "spend": sp,
                    "fee": fee,
                    "winner": tk["winner"],
                    "mom": momentum,
                }
                traded_slugs.add(tk["slug"])
                skips["entered"] += 1

    if pos is not None:
        won = pos["winner"] == "Up"
        payout = pos["shares"] if won else 0.0
        pnl_g = payout - pos["spend"]
        pnl_n = payout - pos["spend"] - pos["fee"]
        trades.append(
            Trade(
                slug=pos["slug"],
                strategy=name,
                family="cloddsbot",
                side="Up",
                entry_t=pos["entry_t"],
                entry_up=pos["entry_px"],
                entry_down=0.0,
                fill_mode="mid_taker",
                shares=pos["shares"],
                notional=pos["spend"],
                fee=pos["fee"],
                edge_gross=pos.get("mom", 0),
                winner=pos["winner"],
                won=won,
                pnl_gross=pnl_g,
                pnl_net=pnl_n,
                exit_reason="resolution",
            )
        )

    if skips["entered"] == 0:
        skips["no_signal"] = len(markets)
    return trades, skips


def write_chinese_report(
    markets: List[MarketData],
    summaries: Dict[str, dict],
    winner_line: str,
) -> None:
    usable = [m for m in markets if m.series]
    ts_min = min(m.window_ts for m in markets)
    ts_max = max(m.window_ts for m in markets)
    up_wins = sum(1 for m in markets if m.winner == "Up")
    dn_wins = sum(1 for m in markets if m.winner == "Down")
    gen = datetime.now(SH_TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")

    lines = []
    lines.append("# polybot vs CloddsBot — BTC 5 分钟 Up/Down 纸面回测对比")
    lines.append("")
    lines.append(f"**生成时间：** {gen}")
    lines.append("**模式：** 仅纸面（PAPER）— 未下任何真实订单。")
    lines.append("**声明：** 纸面盈亏 ≠ 实盘盈亏；历史结果不保证未来。")
    lines.append("")
    lines.append("## 一句话结论")
    lines.append("")
    lines.append(f"**{winner_line}**")
    lines.append("")
    lines.append("## 样本")
    lines.append(f"- 已关闭市场数：**{len(markets)}**（目标 {TARGET_MARKETS}）")
    lines.append(f"- 有价格序列：**{len(usable)}**")
    lines.append(f"- 窗口起止（Asia/Shanghai）：**{ts_to_sh(ts_min)}** → **{ts_to_sh(ts_max)}**")
    lines.append(f"- 结果分布：Up={up_wins}，Down={dn_wins}")
    lines.append(f"- 单笔名义上限：**${MAX_NOTIONAL:.0f}**")
    lines.append(f"- 手续费模型：taker `fee = shares × {FEE_RATE} × p × (1−p)`")
    lines.append("")
    lines.append("## 方法说明")
    lines.append("1. 同一批 `btc-updown-5m-*` 已关闭市场，所有策略共用。")
    lines.append("2. 价格来自 CLOB `/prices-history`（fidelity=1），对齐 Up/Down mid。")
    lines.append("3. **无历史 L2 盘口**；bid/ask 用 mid±0.005 近似。")
    lines.append("4. 入场默认窗口开启后 ≥15 秒。")
    lines.append("")
    lines.append("### polybot 家族（complete-set / Gabagool 逻辑）")
    lines.append("- 条件：`1 - (bid_UP + bid_DOWN) ≥ min_edge` 时买入双边，持有至结算兑换 $1。")
    lines.append("- `min_edge=0.001`：develop.yaml 默认；`min_edge=0.01`：research 回测默认。")
    lines.append("- 门控标签：")
    lines.append("  - `mid_optimistic`：用 mid 当成交价（偏乐观）")
    lines.append("  - `bid_approx`：bid≈mid−0.005，假设 maker 挂单成交、手续费=0（乐观）")
    lines.append("  - `ask_conservative`：ask≈mid+0.005，taker 双边买入（偏保守）")
    lines.append("- 原生面向 15m/1h；本报告**改到 5m** 仅为公平对比。")
    lines.append("")
    lines.append("### CloddsBot 家族")
    lines.append("- **mean_reversion**：lookback=20，入场 z&lt;−2，出场 z&gt;0.5 / 止损10% / 止盈20%（源码默认）。")
    lines.append("- **momentum**：short=5 / long=20，minMomentum=0.05，止损15% / 止盈30%。")
    lines.append("- **updown_arb**：内部 Up+Down，当 ask_sum &lt; 1−0.01（minEdge≈1%，与 `/arbitrage` 默认一致）。")
    lines.append("- 适配说明：单窗 5 分钟点数不足以填满 lookback=20，故 MR/动量在**跨连续市场的 mid 时间序列**上滚动计算；每市场最多一笔，未提前出场则持有至结算。")
    lines.append("")
    lines.append("## 结果总表")
    lines.append("")
    lines.append("| 策略 | 家族 | 笔数 | 市场数 | 胜率 | 毛 PnL | 手续费 | 净 PnL | 最大回撤 | 均边/信号 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k, s in sorted(summaries.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True):
        wr = f"{100*s['win_rate']:.1f}%" if s["win_rate"] is not None else "n/a(配对)"
        ae = f"{100*s['avg_edge_gross']:.2f}%" if s["avg_edge_gross"] is not None else "n/a"
        lines.append(
            f"| `{s['strategy']}` | {s['family']} | {s['trades']} | {s['markets_traded']} | {wr} | "
            f"${s['gross_pnl']:.2f} | ${s['total_fees']:.2f} | **${s['net_pnl']:.2f}** | "
            f"${s['max_drawdown']:.2f} | {ae} |"
        )
    lines.append("")
    lines.append("## 跳过原因（摘要）")
    lines.append("")
    for k, s in summaries.items():
        sk = s.get("skip_reasons") or {}
        if sk:
            lines.append(f"- `{s['strategy']}`: " + ", ".join(f"{a}={b}" for a, b in sk.items()))
    lines.append("")
    lines.append("## 家族对比")
    poly = [s for s in summaries.values() if s["family"] == "polybot"]
    clod = [s for s in summaries.values() if s["family"] == "cloddsbot"]
    best_poly = max(poly, key=lambda x: x["net_pnl"]) if poly else None
    best_clod = max(clod, key=lambda x: x["net_pnl"]) if clod else None
    # CloddsBot suite: sum of three core strategies (MR + mom + arb) OR best-of
    clod_core = [s for s in clod if s["strategy"].startswith("clodds_")]
    suite_net = sum(s["net_pnl"] for s in clod_core)
    lines.append(f"- polybot 家族最优：`{best_poly['strategy'] if best_poly else 'n/a'}` 净 PnL **${best_poly['net_pnl']:.2f}**" if best_poly else "- polybot：无")
    lines.append(f"- CloddsBot 家族最优单策略：`{best_clod['strategy'] if best_clod else 'n/a'}` 净 PnL **${best_clod['net_pnl']:.2f}**" if best_clod else "- CloddsBot：无")
    lines.append(f"- CloddsBot 套件合计（MR+动量+UpDown套利 净 PnL 相加，非组合仓位）：**${suite_net:.2f}**")
    lines.append("")
    lines.append("## 重要局限 / Caveats")
    lines.append("- 纸面成交 ≠ 实盘：无队列、无拒单、无延迟、无部分成交。")
    lines.append("- 无历史 L2；mid±0.005 会高估可成交边。")
    lines.append("- polybot 原生 15m/1h；5m 波动与盘口结构不同。")
    lines.append("- CloddsBot MR/动量跨市场滚动是适配，非源码原样（源码默认 60s 轮询、更长寿命市场）。")
    lines.append("- 配对套利净利在费用后常被吃掉；乐观 mid/bid 门控会虚高。")
    lines.append("- 样本约 200 个连续窗口，非全历史；过拟合风险存在。")
    lines.append("")
    lines.append("## 输出文件")
    lines.append("- `compare/report.md`（本文件）")
    lines.append("- `compare/summary.json`")
    lines.append("- `compare/trades.csv`")
    lines.append("- `compare/per_market.csv`")
    lines.append("- `compare/cache/markets_*.json`（价格缓存，可复用）")
    lines.append("")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    n = TARGET_MARKETS
    force = False
    for a in sys.argv[1:]:
        if a == "--force":
            force = True
        else:
            try:
                n = int(a)
            except ValueError:
                pass

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    markets = collect_or_load(n, force=force)
    if len(markets) < 10:
        print("ERROR: too few markets", file=sys.stderr)
        sys.exit(1)

    all_trades: List[Trade] = []
    summaries: Dict[str, dict] = {}

    # polybot variants
    for edge in POLYBOT_EDGES:
        for gate in ["mid_optimistic", "bid_approx", "ask_conservative"]:
            tr, sk = run_polybot_complete_set(markets, edge, gate)
            name = f"polybot_cs_edge{edge}_{gate}"
            all_trades.extend(tr)
            summaries[name] = summarize(name, "polybot", tr, len(markets), sk)

    # CloddsBot suite
    for runner, fallback_name in [
        (run_clodds_mean_reversion, "clodds_mean_reversion"),
        (run_clodds_momentum, "clodds_momentum"),
        (run_clodds_arb, f"clodds_updown_arb_edge{CLODDS_ARB_MIN_EDGE}"),
    ]:
        tr, sk = runner(markets)
        name = tr[0].strategy if tr else fallback_name
        all_trades.extend(tr)
        summaries[name] = summarize(name, "cloddsbot", tr, len(markets), sk)

    # Family winners
    poly = [s for s in summaries.values() if s["family"] == "polybot"]
    clod = [s for s in summaries.values() if s["family"] == "cloddsbot"]
    best_poly = max(poly, key=lambda x: x["net_pnl"]) if poly else None
    best_clod = max(clod, key=lambda x: x["net_pnl"]) if clod else None
    # Prefer conservative ask gate for "fair" polybot pick
    fair_poly = [s for s in poly if "ask_conservative" in s["strategy"]]
    fair_best_poly = max(fair_poly, key=lambda x: x["net_pnl"]) if fair_poly else best_poly

    if fair_best_poly and best_clod:
        if fair_best_poly["net_pnl"] > best_clod["net_pnl"]:
            winner_line = (
                f"赢家：polybot 家族（保守 ask 门控最优 `{fair_best_poly['strategy']}` "
                f"净 PnL ${fair_best_poly['net_pnl']:.2f}）优于 CloddsBot 最优 "
                f"`{best_clod['strategy']}`（${best_clod['net_pnl']:.2f}）。"
                f"注意：乐观 mid/bid 变体可能虚高；纸面≠实盘；5m≠原生15m。"
            )
        elif best_clod["net_pnl"] > fair_best_poly["net_pnl"]:
            winner_line = (
                f"赢家：CloddsBot 家族（最优 `{best_clod['strategy']}` "
                f"净 PnL ${best_clod['net_pnl']:.2f}）优于 polybot 保守 ask 最优 "
                f"`{fair_best_poly['strategy']}`（${fair_best_poly['net_pnl']:.2f}）。"
                f"注意：纸面成交≠实盘；方向性策略在 5m 上噪声大。"
            )
        else:
            winner_line = (
                f"平手：polybot 保守 ask（${fair_best_poly['net_pnl']:.2f}）与 "
                f"CloddsBot 最优（${best_clod['net_pnl']:.2f}）接近。"
            )
    else:
        winner_line = "数据不足，无法判定赢家。"

    usable = [m for m in markets if m.series]
    summary = {
        "generated_at_asia_shanghai": datetime.now(SH_TZ).isoformat(),
        "paper_only": True,
        "one_line_winner": winner_line,
        "max_notional_usd": MAX_NOTIONAL,
        "fee_model": f"taker fee = shares * {FEE_RATE} * p * (1-p)",
        "sample": {
            "n_markets": len(markets),
            "n_with_series": len(usable),
            "window_ts_min": min(m.window_ts for m in markets),
            "window_ts_max": max(m.window_ts for m in markets),
            "range_asia_shanghai": [
                ts_to_sh(min(m.window_ts for m in markets)),
                ts_to_sh(max(m.window_ts for m in markets)),
            ],
            "up_wins": sum(1 for m in markets if m.winner == "Up"),
            "down_wins": sum(1 for m in markets if m.winner == "Down"),
        },
        "strategies": summaries,
        "family_best": {
            "polybot_any": best_poly,
            "polybot_ask_conservative": fair_best_poly,
            "cloddsbot": best_clod,
            "cloddsbot_suite_sum_net_pnl": sum(s["net_pnl"] for s in clod),
        },
        "caveats": [
            "No live orders",
            "No historical L2 — bid/ask ≈ mid ± 0.005",
            "polybot native 15m/1h adapted to 5m",
            "CloddsBot MR/momentum use cross-market rolling mids (5m window too short for lookback 20)",
            "Paper fills ≠ live fills",
            "Profits not guaranteed",
        ],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    with open(OUT_DIR / "per_market.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["slug", "window_ts", "winner", "volume", "n_pts", "entry_up", "entry_down", "skip_reason"],
        )
        w.writeheader()
        for m in markets:
            w.writerow({
                "slug": m.slug,
                "window_ts": m.window_ts,
                "winner": m.winner,
                "volume": m.volume,
                "n_pts": len(m.series),
                "entry_up": m.entry_up,
                "entry_down": m.entry_down,
                "skip_reason": m.skip_reason,
            })

    with open(OUT_DIR / "trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(all_trades[0]).keys()) if all_trades else [
            "slug", "strategy", "family", "side", "entry_t", "entry_up", "entry_down",
            "fill_mode", "shares", "notional", "fee", "edge_gross", "winner", "won",
            "pnl_gross", "pnl_net", "exit_reason", "skip_note",
        ])
        w.writeheader()
        for t in all_trades:
            w.writerow(asdict(t))

    write_chinese_report(markets, summaries, winner_line)

    print("Wrote:", OUT_DIR / "report.md")
    print("Wrote:", OUT_DIR / "summary.json")
    print("WINNER:", winner_line)
    for k, s in sorted(summaries.items(), key=lambda kv: kv[1]["net_pnl"], reverse=True):
        print(f"  {s['strategy']}: trades={s['trades']} net=${s['net_pnl']:.2f} dd=${s['max_drawdown']:.2f}")


if __name__ == "__main__":
    main()
