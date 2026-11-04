#!/usr/bin/env python3
"""
btc5m_favchase_live.py — Polymarket BTC 5m favorite-chase live (R1 ∪ R2)

Independent third strategy. Does NOT import or modify DAXUE / GUIDED.
Paper backtest size was $5; live default FAVCHASE_POSITION_USD=5.
Raise POLYMARKET_MAX_ORDER_USD in the process env for this launch only
(place_order.mjs hard-caps per process.env).

Rules v1 (no trend3 / no sr1000):
  R2: rem > 60 AND ask >= 0.80 → BUY that side
  R1: rem in (90, 150] AND ask >= 0.72 → BUY that side (if R2 not taken)
  Favorite = first qualifying Up/Down ask (prefer R2, then higher ask)
  R3: skip ask <= 0.40
  Exit: bid >= 0.99 FOK SELL else hold-to-settle
  One entry per 5m window

Arming:
  LIVE only when FAVCHASE_LIVE=1 AND POLYMARKET_LIVE=1 AND FAVCHASE_DRY_RUN not truthy.
  Default (FAVCHASE_LIVE unset/0): dry-run — log would-be orders, no live place.

Orders via node place_order.mjs. Never prints private keys.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

import aiohttp

from favchase_day_risk import DayRiskState

SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
PID_FILE = LOGS_DIR / "favchase_live.pid"
OPEN_POS_FILE = LOGS_DIR / "favchase_open_positions.json"
PLACE_ORDER_JS = SCRIPT_DIR / "place_order.mjs"
TZ_SH = ZoneInfo("Asia/Shanghai")


def _truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_dotenv(path: Optional[Path] = None) -> dict:
    """Load KEY=VALUE from .env into os.environ (do not overwrite existing). Never log secrets."""
    p = path or (SCRIPT_DIR / ".env")
    out = {}
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        out[k] = v
        if k not in os.environ:
            os.environ[k] = v
    return out


_DOTENV = load_dotenv()

# Prefer Clash 7897; do not overwrite if already set by launcher.
for _k, _v in (
    ("HTTPS_PROXY", "http://127.0.0.1:7897"),
    ("HTTP_PROXY", "http://127.0.0.1:7897"),
    ("ALL_PROXY", "http://127.0.0.1:7897"),
):
    os.environ.setdefault(_k, _v)
os.environ.setdefault("NODE_OPTIONS", "--use-env-proxy")


def _fenv(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return float(default)


def _ienv(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return int(default)


# Paper backtest used $5; live default matches. place_order.mjs still enforces
# POLYMARKET_MAX_ORDER_USD from *this process* env — launch with export=5.
_POS_DEFAULT = 5.0
_pos = _fenv("FAVCHASE_POSITION_USD", _fenv("POSITION_USD", _POS_DEFAULT))
_max_order = _fenv("POLYMARKET_MAX_ORDER_USD", _pos)
# Cap to process max-order; do not silently raise shared .env
POSITION_USD = min(max(0.01, _pos), max(0.01, _max_order))

_live_flag = _truthy(os.getenv("FAVCHASE_LIVE", "0"))
_poly_live = _truthy(os.getenv("POLYMARKET_LIVE", "0"))
_dry_flag = _truthy(os.getenv("FAVCHASE_DRY_RUN", "0"))
# Default dry when FAVCHASE_LIVE unset/0
if not _live_flag:
    _dry_flag = True
_armed_live = bool(_live_flag and _poly_live and not _dry_flag)

CONFIG = {
    "STRATEGY": "FAVCHASE",
    "POLYMARKET_LIVE": _poly_live,
    "FAVCHASE_LIVE": _live_flag,
    "DRY_RUN": (not _armed_live) or _dry_flag,
    "ARMED_LIVE": _armed_live,
    "POSITION_USD": POSITION_USD,
    "PAPER_SIZE_USD": 5.0,
    # R1 / R2 thresholds
    "R1_MIN_ASK": _fenv("FAVCHASE_R1_MIN_ASK", 0.72),
    "R1_REM_LO_EXCL": _fenv("FAVCHASE_R1_REM_LO", 90.0),
    "R1_REM_HI_INCL": _fenv("FAVCHASE_R1_REM_HI", 150.0),
    "R2_MIN_ASK": _fenv("FAVCHASE_R2_MIN_ASK", 0.80),
    "R2_REM_LO_EXCL": _fenv("FAVCHASE_R2_REM_LO", 60.0),
    "R3_SKIP_ASK_LE": _fenv("FAVCHASE_R3_SKIP_LE", 0.40),
    "TP_BID": _fenv("FAVCHASE_TP_BID", 0.99),
    "BUY_SLIP": _fenv("FAVCHASE_BUY_SLIP", 0.02),
    "WINDOW_SEC": 300.0,
    "MAX_RUN_SEC": _fenv("FAVCHASE_MAX_RUN_SEC", _fenv("MAX_RUN_SEC", 3600.0)),
    "POLL_SEC": _fenv("FAVCHASE_POLL_SEC", 3.0),
    "HEARTBEAT_SEC": _fenv("FAVCHASE_HEARTBEAT_SEC", 30.0),
    "PLACE_ORDER_JS": str(PLACE_ORDER_JS),
    "BANKROLL_USD": _fenv("FAVCHASE_BANKROLL_USD", 20.0),
    "MAX_DEPLOY_USD": float(
        os.getenv("FAVCHASE_MAX_DEPLOY_USD")
        or math.floor((2.0 / 3.0) * _fenv("FAVCHASE_BANKROLL_USD", 20.0))
    ),
    # Day risk
    "DAY_LOSS_STOP": _fenv("FAVCHASE_DAY_LOSS_STOP", _fenv("DAY_LOSS_STOP", 10.0)),
    "CONSEC_LOSS_PAUSE": _ienv(
        "FAVCHASE_CONSEC_LOSS_PAUSE",
        int(float(os.getenv("CONSEC_LOSS_PAUSE", "3"))),
    ),
    "PAUSE_MIN": _ienv(
        "FAVCHASE_PAUSE_MIN", int(float(os.getenv("PAUSE_MIN", "30")))
    ),
    "BIG_WIN_LOCK": _fenv("FAVCHASE_BIG_WIN_LOCK", _fenv("BIG_WIN_LOCK", 6.0)),
    "LOCK_GAINS": _truthy(
        os.getenv("FAVCHASE_LOCK_GAINS", os.getenv("LOCK_GAINS", "0"))
    ),
    # Off / high by default for FAVCHASE (optional)
    "DAY_PROFIT_LOCK": _fenv("FAVCHASE_DAY_PROFIT_LOCK", 0.0),
    "DAILY_TARGET_SOFT": 0.0,
    # Session / weekend — FAVCHASE_* preferred, fall back to shared SESSION_HOURS etc.
    "WEEKEND_PAUSE": _truthy(
        os.getenv(
            "FAVCHASE_WEEKEND_PAUSE",
            os.getenv("WEEKEND_PAUSE", "1"),
        )
    ),
    "WEEKEND_PAUSE_TZ": os.getenv(
        "FAVCHASE_WEEKEND_PAUSE_TZ",
        os.getenv("WEEKEND_PAUSE_TZ", "Asia/Shanghai"),
    ),
    "SESSION_HOURS": _truthy(
        os.getenv(
            "FAVCHASE_SESSION_HOURS",
            os.getenv("SESSION_HOURS", "1"),
        )
    ),
}


def _minute_of_day(dt: datetime) -> float:
    return dt.hour * 60 + dt.minute + dt.second / 60.0


def _as_tz(now: Optional[datetime], tz) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def in_weekend_pause(now: Optional[datetime] = None) -> tuple:
    """True during Asia/Shanghai Sat 12:00 inclusive → Mon 12:00 exclusive."""
    tz_name = str(CONFIG.get("WEEKEND_PAUSE_TZ") or "Asia/Shanghai")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = TZ_SH
    dt = _as_tz(now, tz)
    wd = dt.weekday()
    mod = _minute_of_day(dt)
    noon = 12 * 60
    if wd == 5:
        paused = mod >= noon
    elif wd == 6:
        paused = True
    elif wd == 0:
        paused = mod < noon
    else:
        paused = False
    return paused, dt


def in_session_window(dt: datetime) -> tuple:
    """Day Mon12–16 / Tue–Fri 08–16; Night 20:00–次日 05:00 Asia/Shanghai."""
    wd = dt.weekday()
    mod = _minute_of_day(dt)
    night_start = 20 * 60
    night_end = 5 * 60
    day_end = 16 * 60
    if mod >= night_start or mod < night_end:
        return True, "night"
    if wd == 0:
        if 12 * 60 <= mod < day_end:
            return True, "day"
    elif 1 <= wd <= 4:
        if 8 * 60 <= mod < day_end:
            return True, "day"
    return False, ""


def entries_allowed_now(now: Optional[datetime] = None) -> tuple:
    dt = _as_tz(now, TZ_SH)
    if CONFIG.get("WEEKEND_PAUSE", True):
        paused, _ = in_weekend_pause(dt)
        if paused:
            return False, "weekend_pause", dt, "Sat12-Mon12 Asia/Shanghai"
    if CONFIG.get("SESSION_HOURS", True):
        ok, which = in_session_window(dt)
        if not ok:
            return (
                False,
                "session_hours",
                dt,
                "day Mon12-16/Tue-Fri08-16 + night 20-05 Asia/Shanghai",
            )
        return True, "", dt, which
    return True, "", dt, "session_hours_off"


@dataclass
class Market:
    token_up: str
    token_down: str
    end_ts: float
    question: str = ""
    slug: str = ""
    start_ts: float = 0.0


@dataclass
class OpenPos:
    slug: str
    side: str
    ask: float
    cost_usd: float
    market_end: float
    ts: float
    rule: str = ""
    order_id: str = ""
    live_filled: bool = False
    fill_px: float = 0.0
    decision_ask: float = 0.0
    shares: float = 0.0
    tp_attempted: bool = False
    tp_exited: bool = False
    bad_fill: bool = False


def setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"favchase_live_{stamp}.log"
    logger = logging.getLogger("favchase")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.info(f"[FAVCHASE] log_file={log_path}")
    logger.info(f"[FAVCHASE] note: paper backtest size was $5; live POSITION_USD={CONFIG['POSITION_USD']}")
    return logger


log = setup_logging()


def write_pid() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")


def load_open_positions() -> list:
    if not OPEN_POS_FILE.is_file():
        return []
    try:
        data = json.loads(OPEN_POS_FILE.read_text(encoding="utf-8"))
        raw = data.get("positions") if isinstance(data, dict) else data
        out = []
        for r in raw or []:
            out.append(
                OpenPos(
                    slug=str(r.get("slug") or ""),
                    side=str(r.get("side") or ""),
                    ask=float(r.get("ask") or 0),
                    cost_usd=float(r.get("cost_usd") or 0),
                    market_end=float(r.get("market_end") or 0),
                    ts=float(r.get("ts") or 0),
                    rule=str(r.get("rule") or ""),
                    order_id=str(r.get("order_id") or ""),
                    live_filled=bool(r.get("live_filled")),
                    fill_px=float(r.get("fill_px") or 0),
                    decision_ask=float(r.get("decision_ask") or 0),
                    shares=float(r.get("shares") or 0),
                    tp_attempted=bool(r.get("tp_attempted")),
                    tp_exited=bool(r.get("tp_exited")),
                    bad_fill=bool(r.get("bad_fill", False)),
                )
            )
        return out
    except Exception as e:
        log.warning(f"[FAVCHASE] open_positions load failed: {e}")
        return []


def save_open_positions(positions: list) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": time.time(),
        "positions": [asdict(p) for p in positions],
    }
    OPEN_POS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def create_session():
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


def _tokens_from_market(m: dict, slug: str) -> Tuple[Optional[str], Optional[str], float, float]:
    end_str = m.get("endDate") or m.get("end_date")
    end_ts = 0.0
    if end_str:
        try:
            end_ts = datetime.fromisoformat(end_str.replace("Z", "+00:00")).timestamp()
        except Exception:
            end_ts = 0.0
    tokens = _as_list(m.get("clobTokenIds"))
    outcomes = _as_list(m.get("outcomes"))
    if len(tokens) < 2:
        return None, None, end_ts, 0.0
    token_up = token_down = None
    for i, o in enumerate(outcomes):
        ol = str(o).lower()
        if ol == "up" or "up" in ol or "yes" in ol:
            token_up = str(tokens[i])
        elif ol == "down" or "down" in ol or "no" in ol:
            token_down = str(tokens[i])
    if not token_up or not token_down:
        token_up, token_down = str(tokens[0]), str(tokens[1])
    start_ts = 0.0
    try:
        if str(slug).startswith("btc-updown-5m-"):
            start_ts = float(str(slug).rsplit("-", 1)[-1])
    except Exception:
        start_ts = 0.0
    return token_up, token_down, end_ts, start_ts


async def fetch_market_by_slug(session, slug: str) -> Optional[Market]:
    try:
        async with session.get(
            f"https://gamma-api.polymarket.com/markets/slug/{slug}"
        ) as r:
            if r.status != 200:
                return None
            m = await r.json()
    except Exception as e:
        log.warning(f"[FAVCHASE] Gamma slug fetch {slug}: {e}")
        return None
    if not isinstance(m, dict):
        return None
    token_up, token_down, end_ts, start_ts = _tokens_from_market(m, slug)
    if not token_up or not token_down:
        return None
    return Market(
        token_up=token_up,
        token_down=token_down,
        end_ts=end_ts,
        question=(m.get("question") or slug),
        slug=slug,
        start_ts=start_ts,
    )


async def find_btc5m(session) -> Optional[Market]:
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
            log.error(f"[FAVCHASE] Gamma slug {slug}: {e}")
            continue
        if not isinstance(m, dict) or m.get("closed") is True:
            continue
        token_up, token_down, end_ts, start_ts = _tokens_from_market(m, slug)
        if not token_up or not token_down or end_ts <= 0:
            continue
        if not (15 < end_ts - now < 320):
            continue
        return Market(
            token_up=token_up,
            token_down=token_down,
            end_ts=end_ts,
            question=(m.get("question") or slug),
            slug=slug,
            start_ts=float(start_ts or start),
        )
    return None


async def get_book(session, token_id: str) -> dict:
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
    return min(float(a["price"]) for a in asks)


def best_bid(book: dict) -> float:
    bids = book.get("bids") or []
    if not bids:
        return 0.0
    try:
        return max(float(b["price"]) for b in bids)
    except (TypeError, ValueError, KeyError):
        return 0.0


def rem_sec(market: Market, now: float) -> float:
    """Seconds remaining in window. Prefer end_ts; fall back to start+300."""
    if market.end_ts > 0:
        return float(market.end_ts) - float(now)
    if market.start_ts > 0:
        return float(market.start_ts) + float(CONFIG["WINDOW_SEC"]) - float(now)
    return 0.0


def classify_rule(ask: float, rem: float) -> Optional[str]:
    """Return 'R2' / 'R1' / None. R3 skip ask<=0.40."""
    r3 = float(CONFIG["R3_SKIP_ASK_LE"])
    if ask <= r3 + 1e-12:
        return None
    r2_min = float(CONFIG["R2_MIN_ASK"])
    r2_lo = float(CONFIG["R2_REM_LO_EXCL"])
    if ask + 1e-12 >= r2_min and rem > r2_lo:
        return "R2"
    r1_min = float(CONFIG["R1_MIN_ASK"])
    r1_lo = float(CONFIG["R1_REM_LO_EXCL"])
    r1_hi = float(CONFIG["R1_REM_HI_INCL"])
    if ask + 1e-12 >= r1_min and rem > r1_lo and rem <= r1_hi + 1e-12:
        return "R1"
    return None


def pick_favorite(up_ask: float, down_ask: float, rem: float) -> Optional[Tuple[str, float, str]]:
    """Prefer R2 over R1; if both sides same rule, pick higher ask.

    Returns (side UP|DOWN, ask, rule) or None.
    """
    cands = []
    for side, ask in (("UP", up_ask), ("DOWN", down_ask)):
        rule = classify_rule(ask, rem)
        if rule:
            cands.append((side, ask, rule))
    if not cands:
        return None
    r2 = [c for c in cands if c[2] == "R2"]
    pool = r2 if r2 else cands
    # highest ask wins among preferred rule
    pool.sort(key=lambda x: x[1], reverse=True)
    return pool[0]


def place_order_sync(
    slug: str,
    outcome: str,
    side: str,
    amount: float,
    price: Optional[float] = None,
    dry: bool = False,
    max_price: Optional[float] = None,
    decision_ask: Optional[float] = None,
    shares: Optional[float] = None,
) -> dict:
    """Call node place_order.mjs; never logs secrets."""
    js = CONFIG.get("PLACE_ORDER_JS") or str(PLACE_ORDER_JS)
    amt = min(float(amount), float(CONFIG["POSITION_USD"]))
    if amt <= 0:
        return {"ok": False, "error": "amount_zero"}
    cmd = [
        "node",
        js,
        "--slug",
        str(slug),
        "--outcome",
        str(outcome).lower(),
        "--side",
        str(side).upper(),
        "--amount",
        f"{max(amt, 0.01):.2f}",
    ]
    if decision_ask is not None and decision_ask > 0:
        cmd.extend(["--decision-ask", f"{float(decision_ask):.4f}"])
    if shares is not None and float(shares) > 0:
        cmd.extend(["--shares", f"{float(shares):.6f}"])
    if max_price is not None and max_price > 0:
        cmd.extend(["--max-price", f"{float(max_price):.4f}"])
    elif price is not None and price > 0:
        cmd.extend(["--price", f"{float(price):.4f}"])
    if dry:
        cmd.append("--dry")
    env = os.environ.copy()
    env.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
    env.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")
    env.setdefault("ALL_PROXY", "http://127.0.0.1:7897")
    env.setdefault("NODE_OPTIONS", "--use-env-proxy")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return {"ok": False, "error": f"subprocess: {e}"}
    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    parsed = None
    try:
        if raw:
            try:
                parsed = json.loads(raw)
            except Exception:
                i = raw.rfind("{")
                if i >= 0:
                    parsed = json.loads(raw[i:])
    except Exception:
        parsed = None
    ok = (
        proc.returncode == 0
        and isinstance(parsed, dict)
        and parsed.get("ok", True) is not False
    )
    if isinstance(parsed, dict):
        parsed = dict(parsed)
        parsed["ok"] = bool(ok)
        parsed["_returncode"] = proc.returncode
        return parsed
    return {
        "ok": False,
        "error": "bad_response",
        "_returncode": proc.returncode,
        "_stdout_tail": raw[-300:] if raw else "",
    }


async def place_order(
    slug: str,
    outcome: str,
    side: str,
    amount: float,
    price: Optional[float] = None,
    dry: bool = False,
    max_price: Optional[float] = None,
    decision_ask: Optional[float] = None,
    shares: Optional[float] = None,
) -> dict:
    return await asyncio.to_thread(
        place_order_sync,
        slug,
        outcome,
        side,
        amount,
        price,
        dry,
        max_price,
        decision_ask,
        shares,
    )


def extract_fill_px(resp: dict) -> Optional[float]:
    if not isinstance(resp, dict):
        return None
    for k in ("fill_px", "fillPx", "avg_price", "averagePrice"):
        try:
            v = float(resp.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    r = resp.get("response")
    if isinstance(r, dict):
        try:
            taking = float(r.get("takingAmount") or r.get("taking_amount") or 0)
            making = float(r.get("makingAmount") or r.get("making_amount") or 0)
            if taking > 0 and making > 0:
                return making / taking
        except (TypeError, ValueError):
            pass
    return None


async def check_geoblock(session) -> dict:
    def _curl():
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "-m",
                    "10",
                    "-x",
                    os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7897"),
                    "https://polymarket.com/api/geoblock",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode != 0:
                return {"error": proc.stderr.strip() or f"curl {proc.returncode}"}
            return json.loads(proc.stdout)
        except Exception as e:
            return {"error": str(e)}

    try:
        return await asyncio.to_thread(_curl)
    except Exception as e:
        return {"error": str(e)}


class FavChaseBot:
    def __init__(self):
        self.open_positions = load_open_positions()
        self.day_risk = DayRiskState(LOGS_DIR, CONFIG)
        self._entered_slugs: set = set()
        self._last_hb = 0.0
        self._t0 = time.time()
        for p in self.open_positions:
            if p.slug:
                self._entered_slugs.add(p.slug)

    def open_cost(self) -> float:
        return sum(float(p.cost_usd) for p in self.open_positions)

    def _pos_shares(self, p: OpenPos) -> float:
        if float(getattr(p, "shares", 0) or 0) > 0:
            return float(p.shares)
        entry = float(p.fill_px or p.decision_ask or p.ask or 0)
        if entry <= 0 or float(p.cost_usd) <= 0:
            return 0.0
        return float(p.cost_usd) / entry

    def prune_settled(self, now: float) -> None:
        grace = 30.0
        kept = []
        freed = []
        for p in self.open_positions:
            if p.market_end > 0 and now >= p.market_end + grace:
                log.info(
                    f"[FAVCHASE][SETTLE] free slot {p.side} slug={p.slug} "
                    f"rule={p.rule} cost=${p.cost_usd:.2f} market_end={int(p.market_end)}"
                )
                freed.append(p)
            else:
                kept.append(p)
        if freed:
            self.open_positions = kept
            save_open_positions(self.open_positions)
            for p in freed:
                if not p.live_filled and CONFIG["DRY_RUN"]:
                    continue
                self.day_risk.queue_pending(p)
                self.day_risk.try_settle_one(
                    p.slug,
                    {
                        "side": p.side,
                        "cost_usd": p.cost_usd,
                        "fill_px": p.fill_px,
                        "decision_ask": p.decision_ask or p.ask,
                    },
                )
            self.day_risk.check_day_loss(self.open_positions)

    async def maybe_tp_exit(self, session, now: float) -> None:
        """FOK SELL when best bid >= TP_BID (default 0.99); else hold to settle."""
        if not self.open_positions:
            return
        tp = float(CONFIG["TP_BID"])
        dry = bool(CONFIG["DRY_RUN"])
        for p in list(self.open_positions):
            if getattr(p, "tp_attempted", False) or getattr(p, "tp_exited", False):
                continue
            if not p.live_filled and not dry:
                continue
            if p.market_end > 0 and now >= float(p.market_end):
                continue
            shares = self._pos_shares(p)
            if shares <= 0:
                continue
            mkt = await fetch_market_by_slug(session, p.slug)
            if not mkt:
                continue
            token = mkt.token_up if p.side == "UP" else mkt.token_down
            book = await get_book(session, token)
            bid = best_bid(book)
            if bid + 1e-12 < tp:
                continue
            log.info(
                f"[FAVCHASE][TP] trigger {p.side} slug={p.slug} rule={p.rule} "
                f"best_bid={bid:.3f} tp={tp:.2f} shares={shares:.4f} "
                f"entry_px={float(p.fill_px or p.decision_ask or p.ask):.3f} "
                f"cost=${float(p.cost_usd):.2f} minPrice={tp:.2f} FOK"
            )
            p.tp_attempted = True
            save_open_positions(self.open_positions)
            resp = await place_order(
                p.slug,
                p.side.lower(),
                "SELL",
                float(CONFIG["POSITION_USD"]),
                price=tp,
                dry=dry,
                shares=shares,
                decision_ask=float(p.decision_ask or p.ask or 0) or None,
            )
            ok = bool(resp.get("ok"))
            sell_px = extract_fill_px(resp)
            if ok:
                if sell_px is None or sell_px <= 0:
                    sell_px = bid
                proceeds = float(shares) * float(sell_px)
                pnl = proceeds - float(p.cost_usd)
                p.tp_exited = True
                self.open_positions = [
                    x for x in self.open_positions if x is not p and x.slug != p.slug
                ]
                save_open_positions(self.open_positions)
                result = "W" if pnl >= 0 else "L"
                try:
                    self.day_risk.record_settled(
                        slug=p.slug,
                        side=p.side,
                        result=result,
                        pnl=pnl,
                        fill_px=float(p.fill_px or 0),
                        decision_ask=float(p.decision_ask or p.ask or 0),
                        cost_usd=float(p.cost_usd),
                        exit_px=float(sell_px),
                    )
                    self.day_risk.check_day_loss(self.open_positions)
                except Exception as e:
                    log.warning(f"[FAVCHASE][TP] day_risk record failed: {e}")
                log.info(
                    f"[FAVCHASE][TP] success {result} {p.side} slug={p.slug} "
                    f"sell_px={float(sell_px):.3f} best_bid={bid:.3f} "
                    f"shares={shares:.4f} proceeds=${proceeds:.4f} "
                    f"cost=${float(p.cost_usd):.2f} pnl=${pnl:+.4f} dry={int(dry)}"
                )
            else:
                err = (
                    resp.get("error")
                    or resp.get("message")
                    or resp.get("_stdout_tail")
                    or str(resp)[:200]
                )
                log.warning(
                    f"[FAVCHASE][TP] fail {p.side} slug={p.slug} "
                    f"best_bid={bid:.3f} minPrice={tp:.2f} err={err} — hold_to_settle"
                )

    async def maybe_enter(self, session, market: Market, rem: float, now: float) -> None:
        dry = bool(CONFIG["DRY_RUN"])
        if not CONFIG["POLYMARKET_LIVE"] and not dry:
            log.warning("[FAVCHASE][SKIP] POLYMARKET_LIVE!=1 and not dry-run")
            return

        allowed, reason, local_dt, detail = entries_allowed_now()
        if not allowed:
            # throttle: only log on heartbeat path; brief note here occasionally
            return

        block = self.day_risk.entry_block_reason(self.open_positions, now)
        if block:
            log.info(
                f"[FAVCHASE][SKIP] {block} day_pnl_settled=${self.day_risk.day_pnl_settled:+.2f} "
                f"for_stop=${self.day_risk.day_pnl_for_stop(self.open_positions):+.2f} "
                f"streak_L={self.day_risk.consec_losses} rem={rem:.0f}s slug={market.slug}"
            )
            return

        if rem <= 0:
            return
        if market.slug in self._entered_slugs:
            return
        if any(p.slug == market.slug for p in self.open_positions):
            self._entered_slugs.add(market.slug)
            return

        cost = float(CONFIG["POSITION_USD"])
        max_deploy = float(CONFIG["MAX_DEPLOY_USD"])
        cur = self.open_cost()
        if cur + cost > max_deploy + 1e-9:
            log.info(
                f"[FAVCHASE][SKIP] max-deploy open_cost=${cur:.2f}+${cost:.2f}>"
                f"${max_deploy:.0f} rem={rem:.0f}s slug={market.slug}"
            )
            return

        book_up = await get_book(session, market.token_up)
        book_dn = await get_book(session, market.token_down)
        up_ask = best_ask(book_up)
        dn_ask = best_ask(book_dn)
        pick = pick_favorite(up_ask, dn_ask, rem)
        if not pick:
            return
        side, ask, rule = pick

        # Ambiguous dual: both sides qualify same rule with close asks — still pick highest
        # (documented). Log both asks for audit.
        decision_ask = float(ask)
        slip = float(CONFIG["BUY_SLIP"])
        buy_cap = min(0.99, decision_ask + slip)

        self._entered_slugs.add(market.slug)
        mode = "DRY" if dry else "LIVE"
        log.info(
            f"[FAVCHASE][{mode}][ENTRY] {rule} {side} ask={ask:.3f} "
            f"up_ask={up_ask:.3f} down_ask={dn_ask:.3f} rem={rem:.0f}s "
            f"decision_ask={decision_ask:.3f} max_price={buy_cap:.3f} "
            f"cost=${cost:.2f} open_cost=${cur:.2f}/${max_deploy:.0f} "
            f"slug={market.slug}"
        )

        resp = await place_order(
            market.slug,
            side.lower(),
            "BUY",
            cost,
            price=decision_ask,
            dry=dry,
            max_price=buy_cap,
            decision_ask=decision_ask,
        )
        ok = bool(resp.get("ok"))
        order_id = ""
        if isinstance(resp.get("response"), dict):
            order_id = str(
                resp["response"].get("orderID")
                or resp["response"].get("orderId")
                or resp["response"].get("id")
                or ""
            )
        fill_px = extract_fill_px(resp)
        if ok:
            entry_px = float(fill_px or decision_ask or ask or 0)
            shares_est = (cost / entry_px) if entry_px > 0 else 0.0
            pos = OpenPos(
                slug=market.slug,
                side=side,
                ask=ask,
                cost_usd=cost,
                market_end=market.end_ts,
                ts=now,
                rule=rule,
                order_id=order_id,
                live_filled=not dry,
                fill_px=float(fill_px or 0),
                decision_ask=decision_ask,
                shares=float(shares_est),
            )
            self.open_positions.append(pos)
            save_open_positions(self.open_positions)
            tag = "DRY" if dry else "FILL"
            log.info(
                f"[FAVCHASE][{tag}] {rule} {side} decision_ask={decision_ask:.3f} "
                f"fill_px={fill_px if fill_px is not None else '-'} "
                f"max_price={buy_cap:.3f} slug={market.slug} "
                f"order_id={order_id or '-'} resp_ok=1"
            )
        else:
            self._entered_slugs.discard(market.slug)
            err = resp.get("error") or resp.get("_stdout_tail") or str(resp)[:200]
            log.warning(
                f"[FAVCHASE][SKIP] order-fail {rule} {side} decision_ask={decision_ask:.3f} "
                f"max_price={buy_cap:.3f} slug={market.slug} err={err}"
            )

    async def run(self):
        write_pid()
        session = await create_session()
        try:
            geo = await check_geoblock(session)
            blocked = geo.get("blocked")
            if blocked is True:
                log.warning(f"[FAVCHASE][WARN] geoblock blocked=true geo={geo}")
            else:
                log.info(f"[FAVCHASE] geoblock blocked={blocked} geo={geo}")

            max_run = float(CONFIG["MAX_RUN_SEC"] or 0)
            mode = (
                "FAVCHASE_LIVE"
                if CONFIG["ARMED_LIVE"]
                else ("FAVCHASE_DRY" if CONFIG["DRY_RUN"] else "FAVCHASE_DISARMED")
            )
            allowed, reason, local_dt, detail = entries_allowed_now()
            log.info(
                f"Started | mode={mode} | strategy=favorite-chase R1∪R2 | "
                f"FAVCHASE_LIVE={CONFIG['FAVCHASE_LIVE']} | "
                f"POLYMARKET_LIVE={CONFIG['POLYMARKET_LIVE']} | "
                f"DRY_RUN={CONFIG['DRY_RUN']} | "
                f"POSITION_USD={CONFIG['POSITION_USD']} (paper was ${CONFIG['PAPER_SIZE_USD']:.0f}) | "
                f"R2 ask>={CONFIG['R2_MIN_ASK']} rem>{CONFIG['R2_REM_LO_EXCL']} | "
                f"R1 ask>={CONFIG['R1_MIN_ASK']} rem=({CONFIG['R1_REM_LO_EXCL']},{CONFIG['R1_REM_HI_INCL']}] | "
                f"R3 skip ask<={CONFIG['R3_SKIP_ASK_LE']} | "
                f"TP_BID={CONFIG['TP_BID']} | "
                f"bankroll=${CONFIG['BANKROLL_USD']:.0f} max_deploy=${CONFIG['MAX_DEPLOY_USD']:.0f} | "
                f"MAX_RUN_SEC={int(max_run) if max_run else 0} | "
                f"POLL_SEC={CONFIG['POLL_SEC']} | "
                f"DAY_LOSS_STOP=${CONFIG['DAY_LOSS_STOP']:.2f} | "
                f"CONSEC_LOSS_PAUSE={CONFIG['CONSEC_LOSS_PAUSE']} | "
                f"PAUSE_MIN={CONFIG['PAUSE_MIN']} | "
                f"BIG_WIN_LOCK=${CONFIG['BIG_WIN_LOCK']:.2f} | "
                f"LOCK_GAINS={int(bool(CONFIG['LOCK_GAINS']))} | "
                f"DAY_PROFIT_LOCK=${CONFIG['DAY_PROFIT_LOCK']:.2f} | "
                f"weekend_pause={'ON' if CONFIG['WEEKEND_PAUSE'] else 'OFF'} "
                f"Asia/Shanghai Sat12-Mon12 tz={CONFIG['WEEKEND_PAUSE_TZ']} | "
                f"session_hours={'ON' if CONFIG['SESSION_HOURS'] else 'OFF'} "
                f"day=Mon12-16/Tue-Fri08-16 night=20-05 | "
                f"entries_now={'YES '+str(detail) if allowed else 'NO '+reason} | "
                f"pid={os.getpid()} | "
                f"day={self.day_risk.day} day_pnl_settled=${self.day_risk.day_pnl_settled:+.2f} | "
                f"open_restored={len(self.open_positions)} | "
                f"proxy={os.environ.get('HTTPS_PROXY','')} | "
                f"POLYMARKET_MAX_ORDER_USD={os.environ.get('POLYMARKET_MAX_ORDER_USD','(unset)')}"
            )

            while True:
                now = time.time()
                if max_run > 0 and (now - self._t0) >= max_run:
                    log.info(
                        f"[FAVCHASE] MAX_RUN_SEC={int(max_run)} reached — stopping cleanly"
                    )
                    break

                self.day_risk.rollover_if_needed()
                try:
                    self.prune_settled(now)
                except Exception as e:
                    log.error(f"[FAVCHASE] prune_settled error: {e}")
                try:
                    self.day_risk.retry_pending()
                except Exception as e:
                    log.warning(f"[FAVCHASE] retry_pending: {e}")

                market = await find_btc5m(session)
                if not market:
                    if now - self._last_hb >= CONFIG["HEARTBEAT_SEC"]:
                        self._last_hb = now
                        log.info(
                            f"[FAVCHASE] heartbeat: no market | "
                            f"open_pos={len(self.open_positions)} cost=${self.open_cost():.2f}"
                        )
                    await asyncio.sleep(CONFIG["POLL_SEC"])
                    continue

                rem = rem_sec(market, now)
                try:
                    await self.maybe_tp_exit(session, now)
                except Exception as e:
                    log.error(f"[FAVCHASE] maybe_tp_exit error: {e}")
                try:
                    await self.maybe_enter(session, market, rem, now)
                except Exception as e:
                    log.error(f"[FAVCHASE] maybe_enter error: {e}")

                if now - self._last_hb >= CONFIG["HEARTBEAT_SEC"]:
                    self._last_hb = now
                    dr = self.day_risk
                    stop_tag = (
                        "DAY_LOSS_STOP"
                        if dr.day_loss_stopped
                        else (
                            "LOCK_GAINS"
                            if dr.lock_gains_stopped
                            else (
                                "DAY_PROFIT_LOCK"
                                if dr.day_profit_stopped
                                else (
                                    "CONSEC_PAUSE"
                                    if dr.pause_until > now
                                    else "ok"
                                )
                            )
                        )
                    )
                    allowed, reason, _, detail = entries_allowed_now()
                    # light book peek for HB
                    try:
                        bu = await get_book(session, market.token_up)
                        bd = await get_book(session, market.token_down)
                        ua, da = best_ask(bu), best_ask(bd)
                    except Exception:
                        ua, da = -1.0, -1.0
                    hint = pick_favorite(ua, da, rem) if ua >= 0 else None
                    hint_s = (
                        f"{hint[2]}:{hint[0]}@{hint[1]:.3f}" if hint else "-"
                    )
                    log.info(
                        f"[FAVCHASE] heartbeat: rem={rem:.0f}s hint={hint_s} "
                        f"up_ask={ua:.3f} down_ask={da:.3f} "
                        f"open_pos={len(self.open_positions)} "
                        f"cost=${self.open_cost():.2f}/${CONFIG['MAX_DEPLOY_USD']:.0f} "
                        f"day_pnl=${dr.day_pnl_settled:+.2f} "
                        f"for_stop=${dr.day_pnl_for_stop(self.open_positions):+.2f} "
                        f"streak_L={dr.consec_losses} risk={stop_tag} "
                        f"session={'ok:'+detail if allowed else reason} "
                        f"slug={market.slug}"
                    )

                await asyncio.sleep(CONFIG["POLL_SEC"])
        finally:
            await session.close()
            save_open_positions(self.open_positions)
            log.info(
                f"[FAVCHASE] stopped | open_pos={len(self.open_positions)} "
                f"cost=${self.open_cost():.2f}"
            )


def main():
    bot = FavChaseBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("[FAVCHASE] KeyboardInterrupt — exit")
    finally:
        try:
            if PID_FILE.is_file() and PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
