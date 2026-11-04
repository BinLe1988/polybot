#!/usr/bin/env python3
"""Manual favorite-chase backtest draft (paper only).

Encodes 2026-11-03 00:00–03:00 Asia/Shanghai post-mortem rules R1/R2 (+ R3 skip).

How to run (from /Users/nick/poly-test):
  # Offline replay of saved session tape (no network):
  python3 backtests/manual_fav_chase_backtest.py --session 20261103_0000_0300
  python3 backtests/manual_fav_chase_backtest.py --session 20261103_0000_0300 --rule R1
  python3 backtests/manual_fav_chase_backtest.py --session 20261103_0000_0300 --rule R2
  python3 backtests/manual_fav_chase_backtest.py --session 20261103_0000_0300 --rule ALL

  # Multi-day TRUE fill-tape (dump wallet activity first, then session replay):
  python3 backtests/dump_manual_activity.py --days 7 --session week_20261027_1103
  python3 backtests/manual_fav_chase_backtest.py --session week_20261027_1103 --rule ALL

  # Optional multi-day mid±half-spread stub (needs network + CLOB prices-history):
  python3 backtests/manual_fav_chase_backtest.py --fetch --days 7 --rule ALL --max-markets 0
  python3 backtests/manual_fav_chase_backtest.py --fetch --start-sh 2026-10-27 --end-sh 2026-11-02 --rule R2 --max-markets 288

Rule defs (from manual_review summary):
  R1: BUY when fill/ask >= 0.65 AND rem ∈ (120, 240];
      SELL when bid >= 0.99 else hold-to-settle; size $5; one side per window (skip dual).
  R2: BUY only fill >= 0.80; TP 0.99; skip rem < 60; same size / one-side.
  R3: SKIP buys with fill <= 0.40 in this regime (documented filter; not a trade signal).

Clock:
  Asia/Shanghai day windows; slug epoch = windowStart; rem = windowStart + 300 − entry_ts.

Limitations (CANNOT prove from this draft):
  - Human latency / click timing / partial fills (no L2 + own order log)
  - Maker vs taker fee per fill (activity has no fee field)
  - Exact ask depth available at click
  - Statistical validation (one 3h session, n~dozens RTs)
  - Multi-day path needs dumped N-day activity OR public mid/prices-history + settle labels

Patterns reused from backtests/run_backtest.py and compare_bots_backtest.py
(CLOB prices-history, Gamma event slug, fee formula). Does NOT touch .env / live bots.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Prefer local Clash proxy only when reachable; never read trading .env.
def _proxy_ok(port: int = 7890) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


if _proxy_ok(7890):
    for k, v in {
        "HTTPS_PROXY": "http://127.0.0.1:7890",
        "HTTP_PROXY": "http://127.0.0.1:7890",
        "ALL_PROXY": "http://127.0.0.1:7890",
    }.items():
        os.environ.setdefault(k, v)
elif _proxy_ok(7897):
    for k, v in {
        "HTTPS_PROXY": "http://127.0.0.1:7897",
        "HTTP_PROXY": "http://127.0.0.1:7897",
        "ALL_PROXY": "http://127.0.0.1:7897",
    }.items():
        os.environ.setdefault(k, v)
else:
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(k, None)

ROOT = Path(__file__).resolve().parent.parent
BACKTESTS = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
OUT_DIR = BACKTESTS / "manual_fav_chase"
SH_TZ = timezone(timedelta(hours=8))
WINDOW_SEC = 300
SIZE_USD = 5.0
FEE_RATE = 0.07  # same taker model as run_backtest.py (optional; session PnL is pre-fee)
HALF_SPREAD = 0.005  # mid→ask/bid approx when only mid history exists
TP_BID = 0.99

SLUG_RE = re.compile(r"btc-updown-5m-(\d+)$")


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleSpec:
    name: str
    min_buy_px: float
    rem_lo_excl: float  # rem must be > rem_lo_excl
    rem_hi_incl: float  # rem must be <= rem_hi_incl (use +inf for open)
    tp_bid: float
    skip_buy_le: Optional[float] = 0.40  # R3 filter
    one_side_per_window: bool = True
    size_usd: float = SIZE_USD


RULES: Dict[str, RuleSpec] = {
    "R1": RuleSpec(
        name="R1",
        min_buy_px=0.65,
        rem_lo_excl=120.0,
        rem_hi_incl=240.0,
        tp_bid=TP_BID,
    ),
    "R2": RuleSpec(
        name="R2",
        min_buy_px=0.80,
        rem_lo_excl=60.0,  # skip rem < 60 ⇒ rem > 60
        rem_hi_incl=float("inf"),
        tp_bid=TP_BID,
    ),
}


def rem_at(window_start: int, entry_ts: int) -> int:
    return int(window_start) + WINDOW_SEC - int(entry_ts)


def window_start_from_slug(slug: str) -> Optional[int]:
    m = SLUG_RE.search(slug or "")
    return int(m.group(1)) if m else None


def ts_to_sh(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=SH_TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")


def passes_entry(rule: RuleSpec, buy_px: float, rem: float) -> Tuple[bool, str]:
    if rule.skip_buy_le is not None and buy_px <= rule.skip_buy_le:
        return False, "R3_skip_le040"
    if buy_px < rule.min_buy_px:
        return False, f"px<{rule.min_buy_px}"
    if not (rem > rule.rem_lo_excl and rem <= rule.rem_hi_incl):
        return False, f"rem_out_({rule.rem_lo_excl},{rule.rem_hi_incl}]"
    return True, "ok"


# ---------------------------------------------------------------------------
# HTTP helpers (multi-day / --fetch only)
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: float = 30.0, retries: int = 4) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "poly-test-manual-fav-chase/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(0.4 * (2 ** attempt))
    raise RuntimeError(f"GET failed {url}: {last_err}")


def fetch_price_history(token: str, start_ts: int, end_ts: int) -> List[dict]:
    q = urllib.parse.urlencode(
        {"market": token, "startTs": start_ts, "endTs": end_ts, "fidelity": 1}
    )
    data = http_get(f"https://clob.polymarket.com/prices-history?{q}")
    hist = data.get("history") if isinstance(data, dict) else data
    return hist or []


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


def fetch_gamma_event(window_ts: int) -> Optional[dict]:
    data = http_get(f"https://gamma-api.polymarket.com/events?slug=btc-updown-5m-{window_ts}")
    if not data:
        return None
    return data[0]


def taker_fee(shares: float, price: float) -> float:
    p = max(0.0, min(1.0, float(price)))
    return float(shares) * FEE_RATE * p * (1.0 - p)


# ---------------------------------------------------------------------------
# Session tape load + FIFO roundtrips
# ---------------------------------------------------------------------------

@dataclass
class Fill:
    ts: int
    side: str  # BUY | SELL
    slug: str
    outcome: str
    price: float
    size: float
    usdc_size: Optional[float] = None


@dataclass
class RoundTrip:
    slug: str
    outcome: str
    entry_ts: int
    exit_ts: Optional[int]
    buy_px: float
    sell_px: Optional[float]
    shares: float
    cost_usdc: float
    sell_usdc: Optional[float]
    pnl: Optional[float]
    rem_at_entry: int
    tag: str
    window_start: int


def load_session_files(session_id: str) -> Tuple[Path, Optional[Path], Optional[Path]]:
    """Return (activity, summary, summary_slim) paths; raise if activity missing."""
    activity = LOGS / f"manual_activity_{session_id}.json"
    summary = LOGS / f"manual_review_{session_id}_summary.json"
    slim = LOGS / f"manual_review_{session_id}_summary_slim.json"
    if not activity.exists():
        raise FileNotFoundError(
            f"Missing session activity: {activity}\n"
            f"Expected logs/manual_activity_{session_id}.json"
        )
    return activity, (summary if summary.exists() else None), (slim if slim.exists() else None)


def fills_from_activity(activity_path: Path, win_start: Optional[int] = None, win_end: Optional[int] = None) -> List[Fill]:
    data = json.loads(activity_path.read_text(encoding="utf-8"))
    meta = data.get("meta") or {}
    if win_start is None:
        win_start = meta.get("win_start_epoch")
    if win_end is None:
        win_end = meta.get("win_end_epoch")

    # Prefer activity TRADE rows (include usdc_size). `trades` often omits usdc_size
    # and would inflate PnL via size*price. Fall back to trades if activity empty.
    raw = [
        a for a in (data.get("activity") or []) if a.get("type") == "TRADE"
    ]
    if not raw:
        raw = data.get("trades") or []
    out: List[Fill] = []
    for t in raw:
        ts = int(t["timestamp"])
        if win_start is not None and ts < int(win_start):
            continue
        if win_end is not None and ts >= int(win_end):
            continue
        slug = t.get("slug") or t.get("event_slug") or ""
        if not slug:
            continue
        side = str(t.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            continue
        out.append(
            Fill(
                ts=ts,
                side=side,
                slug=slug,
                outcome=str(t.get("outcome") or ("Up" if t.get("outcome_index") == 0 else "Down")),
                price=float(t["price"]),
                size=float(t["size"]),
                usdc_size=float(t["usdc_size"]) if t.get("usdc_size") is not None else None,
            )
        )
    out.sort(key=lambda f: (f.ts, 0 if f.side == "BUY" else 1))
    return out


def fifo_roundtrips(fills: List[Fill]) -> List[RoundTrip]:
    """FIFO BUY→SELL same slug+outcome by shares (matches post-mortem pairing)."""
    books: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    closed: List[RoundTrip] = []

    for f in fills:
        key = (f.slug, f.outcome)
        ws = window_start_from_slug(f.slug)
        if ws is None:
            continue
        if f.side == "BUY":
            cost = f.usdc_size if f.usdc_size is not None else f.size * f.price
            books[key].append(
                {
                    "ts": f.ts,
                    "px": f.price,
                    "shares_left": f.size,
                    "cost_left": cost,
                    "shares0": f.size,
                    "cost0": cost,
                }
            )
            continue

        # SELL
        need = f.size
        sell_px = f.price
        sell_usdc_total = f.usdc_size if f.usdc_size is not None else f.size * f.price
        allocated_shares = 0.0
        while need > 1e-9 and books[key]:
            lot = books[key][0]
            take = min(lot["shares_left"], need)
            frac = take / lot["shares0"] if lot["shares0"] else 0.0
            # cost proportional to remaining lot
            cost_take = lot["cost_left"] * (take / lot["shares_left"]) if lot["shares_left"] else 0.0
            sell_take = sell_usdc_total * (take / f.size) if f.size else 0.0
            pnl = sell_take - cost_take
            rem = rem_at(ws, lot["ts"])
            if sell_px >= 0.95:
                tag = "WIN_099" if sell_px >= 0.99 else "WIN_PARTIAL"
            elif pnl >= 0:
                tag = "WIN_PARTIAL"
            else:
                tag = "STOP_LOSS"
            closed.append(
                RoundTrip(
                    slug=f.slug,
                    outcome=f.outcome,
                    entry_ts=int(lot["ts"]),
                    exit_ts=f.ts,
                    buy_px=float(lot["px"]),
                    sell_px=sell_px,
                    shares=take,
                    cost_usdc=cost_take,
                    sell_usdc=sell_take,
                    pnl=pnl,
                    rem_at_entry=rem,
                    tag=tag,
                    window_start=ws,
                )
            )
            lot["shares_left"] -= take
            lot["cost_left"] -= cost_take
            need -= take
            allocated_shares += take
            if lot["shares_left"] <= 1e-9:
                books[key].pop(0)

    # Unpaired buys → HOLD_REDEEM_OR_OPEN
    for (slug, outcome), lots in books.items():
        ws = window_start_from_slug(slug) or 0
        for lot in lots:
            if lot["shares_left"] <= 1e-9:
                continue
            closed.append(
                RoundTrip(
                    slug=slug,
                    outcome=outcome,
                    entry_ts=int(lot["ts"]),
                    exit_ts=None,
                    buy_px=float(lot["px"]),
                    sell_px=None,
                    shares=float(lot["shares_left"]),
                    cost_usdc=float(lot["cost_left"]),
                    sell_usdc=None,
                    pnl=None,
                    rem_at_entry=rem_at(ws, lot["ts"]),
                    tag="HOLD_REDEEM_OR_OPEN",
                    window_start=ws,
                )
            )
    closed.sort(key=lambda r: (r.entry_ts, r.slug, r.outcome))
    return closed


def dual_side_slugs(rts: List[RoundTrip], meaningful_min_cost: float = 0.5) -> set:
    """Windows where both Up and Down had a meaningful buy (cost>=threshold)."""
    by_slug: Dict[str, set] = defaultdict(set)
    for r in rts:
        if r.cost_usdc >= meaningful_min_cost:
            by_slug[r.slug].add(r.outcome)
    return {s for s, outs in by_slug.items() if len(outs) >= 2}


def scale_pnl_to_size(rt: RoundTrip, size_usd: float) -> Optional[float]:
    """Rescale actual roundtrip PnL to fixed size$ (paper)."""
    if rt.pnl is None or rt.cost_usdc <= 1e-9:
        return None
    return rt.pnl * (size_usd / rt.cost_usdc)


# ---------------------------------------------------------------------------
# Rule application on closed tape
# ---------------------------------------------------------------------------

@dataclass
class SimTrade:
    rule: str
    slug: str
    outcome: str
    entry_ts: int
    exit_ts: Optional[int]
    rem_at_entry: int
    buy_px: float
    sell_px: Optional[float]
    cost_usdc: float
    pnl_actual: Optional[float]
    pnl_size5: Optional[float]
    tag: str
    exit_mode: str  # actual_sell | settle_stub | skipped
    note: str = ""


def apply_rule_on_tape(rule: RuleSpec, rts: List[RoundTrip]) -> List[SimTrade]:
    """
    Filter closed/open roundtrips that would be entries under `rule`.
    One-side: keep first qualifying BUY per window (by entry_ts); skip later / other side.
    Dual windows: skip entire window if any dual-side activity (R1/R2).
    """
    duals = dual_side_slugs(rts) if rule.one_side_per_window else set()
    # Only consider buys (closed or open); for scoring use closed PnL when available.
    candidates = sorted(rts, key=lambda r: (r.entry_ts, r.slug))
    taken_windows: set = set()
    out: List[SimTrade] = []

    for r in candidates:
        ok, why = passes_entry(rule, r.buy_px, float(r.rem_at_entry))
        if not ok:
            continue
        if rule.one_side_per_window and r.slug in duals:
            out.append(
                SimTrade(
                    rule=rule.name,
                    slug=r.slug,
                    outcome=r.outcome,
                    entry_ts=r.entry_ts,
                    exit_ts=r.exit_ts,
                    rem_at_entry=r.rem_at_entry,
                    buy_px=r.buy_px,
                    sell_px=r.sell_px,
                    cost_usdc=r.cost_usdc,
                    pnl_actual=r.pnl,
                    pnl_size5=scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode="skipped",
                    note="skip_dual_side_window",
                )
            )
            continue
        if rule.one_side_per_window and r.slug in taken_windows:
            out.append(
                SimTrade(
                    rule=rule.name,
                    slug=r.slug,
                    outcome=r.outcome,
                    entry_ts=r.entry_ts,
                    exit_ts=r.exit_ts,
                    rem_at_entry=r.rem_at_entry,
                    buy_px=r.buy_px,
                    sell_px=r.sell_px,
                    cost_usdc=r.cost_usdc,
                    pnl_actual=r.pnl,
                    pnl_size5=scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode="skipped",
                    note="skip_second_side_or_repeat",
                )
            )
            continue

        taken_windows.add(r.slug)
        if r.pnl is not None and r.sell_px is not None:
            # Prefer actual exit; if sell < TP and not a clean TP, still count as tape truth.
            exit_mode = "actual_sell"
            if r.sell_px >= rule.tp_bid:
                exit_mode = "actual_tp099"
            out.append(
                SimTrade(
                    rule=rule.name,
                    slug=r.slug,
                    outcome=r.outcome,
                    entry_ts=r.entry_ts,
                    exit_ts=r.exit_ts,
                    rem_at_entry=r.rem_at_entry,
                    buy_px=r.buy_px,
                    sell_px=r.sell_px,
                    cost_usdc=r.cost_usdc,
                    pnl_actual=r.pnl,
                    pnl_size5=scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode=exit_mode,
                    note=why,
                )
            )
        else:
            # Open / unsettled on tape — mark stub (session replay cannot invent settle without Gamma)
            out.append(
                SimTrade(
                    rule=rule.name,
                    slug=r.slug,
                    outcome=r.outcome,
                    entry_ts=r.entry_ts,
                    exit_ts=None,
                    rem_at_entry=r.rem_at_entry,
                    buy_px=r.buy_px,
                    sell_px=None,
                    cost_usdc=r.cost_usdc,
                    pnl_actual=None,
                    pnl_size5=None,
                    tag=r.tag,
                    exit_mode="settle_stub",
                    note="open_on_tape_no_settle_label",
                )
            )
    return out


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def rem_bucket(rem: int) -> str:
    if rem > 240:
        return ">240"
    if rem > 180:
        return "180-240"
    if rem > 120:
        return "120-180"
    if rem > 60:
        return "60-120"
    if rem >= 0:
        return "<60"
    return "unk"


def px_bucket(px: float) -> str:
    if px <= 0.40:
        return "0.00-0.40"
    if px < 0.60:
        return "0.40-0.60"
    if px < 0.80:
        return "0.60-0.80"
    return "0.80+"


def summarize_trades(trades: List[SimTrade], label: str) -> Dict[str, Any]:
    scored = [t for t in trades if t.exit_mode not in ("skipped", "settle_stub") and t.pnl_actual is not None]
    skipped = [t for t in trades if t.exit_mode == "skipped"]
    open_stub = [t for t in trades if t.exit_mode == "settle_stub"]
    wins = [t for t in scored if (t.pnl_actual or 0) > 0]
    pnl = sum(t.pnl_actual or 0 for t in scored)
    pnl5 = sum(t.pnl_size5 or 0 for t in scored if t.pnl_size5 is not None)
    by_rem: Dict[str, Dict[str, Any]] = {}
    by_px: Dict[str, Dict[str, Any]] = {}
    for bucket_fn, store, keyfn in (
        (rem_bucket, by_rem, lambda t: rem_bucket(t.rem_at_entry)),
        (px_bucket, by_px, lambda t: px_bucket(t.buy_px)),
    ):
        groups: Dict[str, List[SimTrade]] = defaultdict(list)
        for t in scored:
            groups[keyfn(t)].append(t)
        for k, xs in groups.items():
            p = sum(x.pnl_actual or 0 for x in xs)
            store[k] = {
                "n": len(xs),
                "pnl": round(p, 4),
                "wr": round(sum(1 for x in xs if (x.pnl_actual or 0) > 0) / len(xs), 4) if xs else None,
                "avg_buy_px": round(sum(x.buy_px for x in xs) / len(xs), 4) if xs else None,
            }
    return {
        "label": label,
        "n_candidates_incl_skips": len(trades),
        "n_scored": len(scored),
        "n_skipped": len(skipped),
        "n_open_stub": len(open_stub),
        "wr": round(len(wins) / len(scored), 4) if scored else None,
        "pnl_actual_sum": round(pnl, 4),
        "pnl_size5_sum": round(pnl5, 4),
        "avg_buy_px": round(sum(t.buy_px for t in scored) / len(scored), 4) if scored else None,
        "avg_rem": round(sum(t.rem_at_entry for t in scored) / len(scored), 1) if scored else None,
        "by_rem": by_rem,
        "by_px": by_px,
        "tags": {k: sum(1 for t in scored if t.tag == k) for k in sorted({t.tag for t in scored})},
    }


# ---------------------------------------------------------------------------
# Multi-day stub: UV HB parse + public mid sim
# ---------------------------------------------------------------------------

HB_RE = re.compile(
    r"\[UV\]\[HB\]\s+slug=(?P<slug>\S+)\s+rem=(?P<rem>[0-9.]+)s\s+"
    r"spot=(?P<spot>[0-9.]+)\s+open=(?P<open>[0-9.]+)"
)


def parse_uv_hb_logs(log_paths: Iterable[Path]) -> Dict[str, List[dict]]:
    """slug → list of {ts_wall_guess, rem, spot, open} from UV HB lines (wall clock = log date approx)."""
    by_slug: Dict[str, List[dict]] = defaultdict(list)
    for path in log_paths:
        if not path.exists():
            continue
        # log lines are HH:MM:SS only; attach file mtime date as weak hint
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = HB_RE.search(line)
            if not m:
                continue
            slug = m.group("slug")
            by_slug[slug].append(
                {
                    "rem": float(m.group("rem")),
                    "spot": float(m.group("spot")),
                    "open": float(m.group("open")),
                    "source": str(path.name),
                }
            )
    return by_slug


def winner_from_hb(rows: List[dict]) -> Optional[str]:
    """Infer Up/Down from last HB spot vs open when both valid."""
    usable = [r for r in rows if r.get("open", 0) and r["open"] > 0 and r.get("spot", 0) > 0]
    if not usable:
        return None
    # Prefer lowest rem (closest to settle)
    last = min(usable, key=lambda r: r["rem"])
    if last["rem"] > 30:
        # weak — still use last known
        pass
    if last["spot"] > last["open"]:
        return "Up"
    if last["spot"] < last["open"]:
        return "Down"
    return None


@dataclass
class MidPoint:
    t: int
    mid: float


def sim_rule_on_mids(
    rule: RuleSpec,
    slug: str,
    window_ts: int,
    up_hist: List[dict],
    down_hist: List[dict],
    winner: str,
) -> Optional[SimTrade]:
    """
    First time ask≈mid+HALF_SPREAD meets rule on either side → buy size$5;
    exit when bid≈mid-HALF_SPREAD >= tp OR settle at 1/0.
    One side per window: first trigger wins.
    """
    series: List[Tuple[int, str, float]] = []
    for h in up_hist:
        t = int(h["t"])
        if window_ts <= t <= window_ts + WINDOW_SEC:
            series.append((t, "Up", float(h["p"])))
    for h in down_hist:
        t = int(h["t"])
        if window_ts <= t <= window_ts + WINDOW_SEC:
            series.append((t, "Down", float(h["p"])))
    series.sort(key=lambda x: x[0])
    if not series:
        return None

    entry: Optional[Tuple[int, str, float]] = None
    for t, side, mid in series:
        ask = min(0.99, mid + HALF_SPREAD)
        rem = rem_at(window_ts, t)
        ok, _ = passes_entry(rule, ask, float(rem))
        if ok:
            entry = (t, side, ask)
            break
    if entry is None:
        return None

    et, side, buy_px = entry
    shares = rule.size_usd / buy_px
    # Look for TP on same side after entry
    sell_px = None
    exit_ts = None
    for t, s, mid in series:
        if s != side or t <= et:
            continue
        bid = max(0.01, mid - HALF_SPREAD)
        if bid >= rule.tp_bid:
            sell_px = rule.tp_bid
            exit_ts = t
            break
    if sell_px is None:
        # settle
        won = winner == side
        sell_px = 1.0 if won else 0.0
        exit_ts = window_ts + WINDOW_SEC
        tag = "SETTLE_WIN" if won else "SETTLE_LOSS"
        exit_mode = "settle"
    else:
        tag = "WIN_099"
        exit_mode = "sim_tp099"
        won = True

    fee_in = taker_fee(shares, buy_px)
    # ignore exit fee for settle redeem; charge on early sell
    fee_out = 0.0 if exit_mode == "settle" else taker_fee(shares, sell_px)
    pnl = shares * sell_px - rule.size_usd - fee_in - fee_out
    return SimTrade(
        rule=rule.name,
        slug=slug,
        outcome=side,
        entry_ts=et,
        exit_ts=exit_ts,
        rem_at_entry=rem_at(window_ts, et),
        buy_px=buy_px,
        sell_px=sell_px,
        cost_usdc=rule.size_usd,
        pnl_actual=pnl,
        pnl_size5=pnl,
        tag=tag,
        exit_mode=exit_mode,
        note="mid_plus_half_spread_sim",
    )


def sh_day_range(start_sh: str, end_sh: str) -> Tuple[int, int]:
    """Inclusive Shanghai calendar dates YYYY-MM-DD → [start_epoch, end_epoch)."""
    d0 = datetime.strptime(start_sh, "%Y-%m-%d").replace(tzinfo=SH_TZ)
    d1 = datetime.strptime(end_sh, "%Y-%m-%d").replace(tzinfo=SH_TZ) + timedelta(days=1)
    return int(d0.timestamp()), int(d1.timestamp())


def iter_window_starts(start_epoch: int, end_epoch: int) -> List[int]:
    t0 = start_epoch - (start_epoch % WINDOW_SEC)
    if t0 < start_epoch:
        t0 += WINDOW_SEC
    out = []
    t = t0
    while t < end_epoch:
        out.append(t)
        t += WINDOW_SEC
    return out


def run_fetch_multiday(rule_names: List[str], start_epoch: int, end_epoch: int, max_markets: int) -> Dict[str, Any]:
    """
    Multi-day path stub with optional live fetch.
    For each 5m window: Gamma settle + CLOB prices-history → sim R1/R2.
    Also joins UV HB logs if present for open/close cross-check.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    uv_logs = sorted(LOGS.glob("undervalued_observe_*.log"))
    hb = parse_uv_hb_logs(uv_logs)
    print(f"[fetch] UV HB logs found: {len(uv_logs)}; slugs_with_hb={len(hb)}", flush=True)

    windows = iter_window_starts(start_epoch, end_epoch)
    if max_markets > 0 and len(windows) > max_markets:
        print(f"[fetch] capping windows {len(windows)} → {max_markets} (pass --max-markets 0 for full range)", flush=True)
        windows = windows[:max_markets]
    print(
        f"[fetch] windows={len(windows)} range={ts_to_sh(start_epoch)} → {ts_to_sh(end_epoch)}",
        flush=True,
    )

    results: Dict[str, List[SimTrade]] = {r: [] for r in rule_names}
    markets_ok = 0
    markets_fail = 0
    for i, ws in enumerate(windows):
        slug = f"btc-updown-5m-{ws}"
        try:
            ev = fetch_gamma_event(ws)
            time.sleep(0.05)
        except Exception as e:
            markets_fail += 1
            if markets_fail <= 5:
                print(f"  warn gamma {slug}: {e}", flush=True)
            continue
        if not ev or not ev.get("closed"):
            continue
        m = ev["markets"][0]
        outs, prices = parse_outcomes(m)
        winner, _, _ = resolve_winner(outs, prices)
        if winner == "unknown":
            # try HB
            winner = winner_from_hb(hb.get(slug, [])) or "unknown"
        if winner == "unknown":
            markets_fail += 1
            continue
        up_tok, dn_tok = parse_tokens(m)
        try:
            up_hist = fetch_price_history(up_tok, ws - 60, ws + 360)
            time.sleep(0.05)
            dn_hist = fetch_price_history(dn_tok, ws - 60, ws + 360)
            time.sleep(0.05)
        except Exception as e:
            markets_fail += 1
            if markets_fail <= 5:
                print(f"  warn prices {slug}: {e}", flush=True)
            continue
        markets_ok += 1
        for rn in rule_names:
            tr = sim_rule_on_mids(RULES[rn], slug, ws, up_hist, dn_hist, winner)
            if tr:
                results[rn].append(tr)
        if (i + 1) % 20 == 0:
            print(f"  … scanned {i+1}/{len(windows)} ok={markets_ok}", flush=True)

    summary = {
        "mode": "fetch_multiday",
        "start": ts_to_sh(start_epoch),
        "end": ts_to_sh(end_epoch),
        "markets_ok": markets_ok,
        "markets_fail_or_skip": markets_fail,
        "rules": {rn: summarize_trades(results[rn], rn) for rn in rule_names},
        "caveats": CAVEATS,
        "still_needs": STILL_NEEDS,
    }
    tag = datetime.fromtimestamp(start_epoch, tz=SH_TZ).strftime("%Y%m%d") + "_" + datetime.fromtimestamp(max(start_epoch, end_epoch - 1), tz=SH_TZ).strftime("%Y%m%d")
    out_path = OUT_DIR / "fetch_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    dated = OUT_DIR / f"fetch_summary_{tag}.json"
    dated.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    trades_path = OUT_DIR / "fetch_trades.csv"
    trades_dated = OUT_DIR / f"fetch_trades_{tag}.csv"
    flat = [t for rn in rule_names for t in results[rn]]
    fields = list(asdict(flat[0]).keys()) if flat else [
        "rule", "slug", "outcome", "entry_ts", "exit_ts", "rem_at_entry",
        "buy_px", "sell_px", "cost_usdc", "pnl_actual", "pnl_size5", "tag", "exit_mode", "note",
    ]
    for path in (trades_path, trades_dated):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in flat:
                w.writerow(asdict(t))
    print(f"Wrote {out_path}")
    print(f"Wrote {dated}")
    print(f"Wrote {trades_path}")
    print(f"Wrote {trades_dated}")
    return summary


CAVEATS = [
    "No live orders; paper only.",
    "Cannot verify human latency / click timing / partial fills without L2 + own order log.",
    "Cannot know maker vs taker fee per fill (activity has no fee field); session PnL is pre-explicit-fee.",
    "Cannot reconstruct ask depth at click; --fetch uses mid ± 0.005 as ask/bid proxy.",
    "One 3h session is high-variance; rules not statistically validated.",
    "Dual-side skip is mechanical (both outcomes bought); no intent log for hedges.",
    "Does not read .env; does not restart DAXUE/undervalued.",
]

STILL_NEEDS = [
    "N-day dumped activity JSON (manual_activity_YYYYMMDD_*.json) for true fill-tape multi-day replay.",
    "Or retained CLOB prices-history for each btc-updown-5m-{epoch} token + Gamma settle labels.",
    "Optional denser UV HB logs covering the day range (open/spot) as settle cross-check.",
    "Optional L2 snapshots if you want ask-at-click fidelity beyond mid±half-spread.",
]


# ---------------------------------------------------------------------------
# Session runner
# ---------------------------------------------------------------------------

def run_session(session_id: str, rule_names: List[str]) -> Dict[str, Any]:
    activity_path, summary_path, slim_path = load_session_files(session_id)
    print(f"[session] activity={activity_path}", flush=True)
    if slim_path:
        print(f"[session] slim_summary={slim_path}", flush=True)

    fills = fills_from_activity(activity_path)
    rts = fifo_roundtrips(fills)
    closed = [r for r in rts if r.pnl is not None]
    print(
        f"[session] fills={len(fills)} roundtrips={len(rts)} closed={len(closed)} "
        f"closed_pnl={sum(r.pnl or 0 for r in closed):.4f}",
        flush=True,
    )

    # Cross-check vs slim summary if present
    cross: Dict[str, Any] = {}
    if slim_path:
        slim = json.loads(slim_path.read_text(encoding="utf-8"))
        cross = {
            "summary_closed_pnl": slim.get("total_pnl_closed_only"),
            "summary_wr": (slim.get("aggregate") or {}).get("wr_count"),
            "summary_n_closed": (slim.get("aggregate") or {}).get("n_roundtrips_closed"),
            "replay_closed_pnl": round(sum(r.pnl or 0 for r in closed), 4),
            "replay_n_closed": len(closed),
            "R1_postmortem": next((x for x in (slim.get("rules") or []) if str(x.get("name", "")).startswith("R1")), None),
            "R2_postmortem": next((x for x in (slim.get("rules") or []) if str(x.get("name", "")).startswith("R2")), None),
            "R3_postmortem": next((x for x in (slim.get("rules") or []) if str(x.get("name", "")).startswith("R3")), None),
        }

    # Baseline: R3 losers on tape (documentation)
    r3 = [
        r for r in closed
        if r.buy_px <= 0.40
    ]

    rule_results: Dict[str, Any] = {}
    all_sim: List[SimTrade] = []
    for rn in rule_names:
        sim = apply_rule_on_tape(RULES[rn], rts)
        # scored only (exclude skipped duals from headline, but keep in dump)
        scored = [t for t in sim if t.exit_mode not in ("skipped",)]
        # For headline WR/PnL match post-mortem: only closed scored
        headline = [t for t in scored if t.pnl_actual is not None]
        rule_results[rn] = summarize_trades(sim, rn)
        rule_results[rn]["headline_closed_n"] = len(headline)
        all_sim.extend(sim)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / f"session_{session_id}_summary.json"
    out_csv = OUT_DIR / f"session_{session_id}_trades.csv"
    payload = {
        "mode": "session_replay",
        "session_id": session_id,
        "generated_at_asia_shanghai": datetime.now(SH_TZ).isoformat(),
        "size_usd_paper": SIZE_USD,
        "rules_text": {
            "R1": "BUY fill/ask>=0.65, rem in (120,240]; SELL bid>=0.99 else hold-settle; $5; one side/window",
            "R2": "BUY fill>=0.80; TP 0.99; skip rem<60; $5; one side/window",
            "R3": "SKIP buy<=0.40 in this regime",
        },
        "tape": {
            "n_fills": len(fills),
            "n_roundtrips": len(rts),
            "n_closed": len(closed),
            "closed_pnl": round(sum(r.pnl or 0 for r in closed), 4),
        },
        "cross_check_summary": cross,
        "R3_skip_filter_on_closed": {
            "n": len(r3),
            "pnl": round(sum(r.pnl or 0 for r in r3), 4),
            "wr": round(sum(1 for r in r3 if (r.pnl or 0) > 0) / len(r3), 4) if r3 else None,
            "note": "These are documented skips — do not enter <=0.40 in this regime",
        },
        "rule_results": rule_results,
        "caveats": CAVEATS,
        "still_needs_for_multiday": STILL_NEEDS,
    }
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        fields = list(asdict(all_sim[0]).keys()) if all_sim else [
            "rule", "slug", "outcome", "entry_ts", "exit_ts", "rem_at_entry",
            "buy_px", "sell_px", "cost_usdc", "pnl_actual", "pnl_size5", "tag", "exit_mode", "note",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for t in all_sim:
            w.writerow(asdict(t))

    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    return payload


def print_session_stdout(payload: Dict[str, Any]) -> None:
    print("\n======== manual_fav_chase session replay ========")
    print(f"session: {payload['session_id']}")
    tape = payload["tape"]
    print(f"tape closed: n={tape['n_closed']} pnl=${tape['closed_pnl']:.4f}")
    cross = payload.get("cross_check_summary") or {}
    if cross.get("summary_closed_pnl") is not None:
        print(
            f"cross-check slim summary: n={cross.get('summary_n_closed')} "
            f"pnl=${cross['summary_closed_pnl']} wr={cross.get('summary_wr')}"
        )
    r3 = payload["R3_skip_filter_on_closed"]
    print(f"R3 skip (buy<=0.40 closed): n={r3['n']} pnl=${r3['pnl']} wr={r3['wr']}")
    for rn, s in payload["rule_results"].items():
        print(
            f"{rn}: scored={s['n_scored']} skipped={s['n_skipped']} "
            f"WR={s['wr']} pnl_actual=${s['pnl_actual_sum']} pnl_size5=${s['pnl_size5_sum']} "
            f"avg_px={s['avg_buy_px']} avg_rem={s['avg_rem']}"
        )
        if s.get("by_rem"):
            print(f"  by_rem: {s['by_rem']}")
        if s.get("by_px"):
            print(f"  by_px: {s['by_px']}")
    print("caveats:")
    for c in CAVEATS:
        print(f"  - {c}")
    print("still needs for multi-day:")
    for c in STILL_NEEDS:
        print(f"  - {c}")
    print("================================================\n")


def print_fetch_stdout(summary: Dict[str, Any]) -> None:
    print("\n======== manual_fav_chase multi-day fetch ========")
    print(f"range: {summary['start']} → {summary['end']}")
    print(f"markets_ok={summary['markets_ok']} fail/skip={summary['markets_fail_or_skip']}")
    for rn, s in summary["rules"].items():
        print(
            f"{rn}: n={s['n_scored']} WR={s['wr']} "
            f"pnl=${s['pnl_actual_sum']} by_rem={s.get('by_rem')}"
        )
    if summary["markets_ok"] == 0:
        print(
            "NOTE: no markets simulated — network/Gamma/history missing or all open. "
            "Session replay still works offline."
        )
    print("================================================\n")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Manual favorite-chase backtest draft (paper only)")
    p.add_argument("--session", type=str, default=None, help="e.g. 20261103_0000_0300 (offline tape replay)")
    p.add_argument("--rule", type=str, default="ALL", help="R1 | R2 | ALL")
    p.add_argument("--fetch", action="store_true", help="Multi-day public mid/history path (network)")
    p.add_argument("--days", type=int, default=0, help="With --fetch: last N Shanghai days ending yesterday")
    p.add_argument("--start-sh", type=str, default=None, help="YYYY-MM-DD Asia/Shanghai")
    p.add_argument("--end-sh", type=str, default=None, help="YYYY-MM-DD Asia/Shanghai inclusive")
    p.add_argument("--max-markets", type=int, default=96, help="Cap windows when --fetch (default 96≈8h; 0=no cap)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    rule_arg = (args.rule or "ALL").upper()
    if rule_arg == "ALL":
        rule_names = ["R1", "R2"]
    elif rule_arg in RULES:
        rule_names = [rule_arg]
    else:
        print(f"Unknown --rule {args.rule}; use R1, R2, or ALL", file=sys.stderr)
        return 2

    if not args.session and not args.fetch:
        print(
            "Specify --session 20261103_0000_0300 (offline) and/or --fetch for multi-day stub.",
            file=sys.stderr,
        )
        return 2

    if args.session:
        try:
            payload = run_session(args.session, rule_names)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        print_session_stdout(payload)

    if args.fetch:
        now_sh = datetime.now(SH_TZ)
        if args.start_sh and args.end_sh:
            start_e, end_e = sh_day_range(args.start_sh, args.end_sh)
        elif args.days and args.days > 0:
            end_day = (now_sh - timedelta(days=1)).strftime("%Y-%m-%d")
            start_day = (now_sh - timedelta(days=args.days)).strftime("%Y-%m-%d")
            start_e, end_e = sh_day_range(start_day, end_day)
        else:
            # default: session day only as smoke
            start_e, end_e = sh_day_range("2026-11-02", "2026-11-02")
            print("[fetch] defaulting to Shanghai day 2026-11-02 (override with --days / --start-sh)", flush=True)
        try:
            summary = run_fetch_multiday(rule_names, start_e, end_e, args.max_markets)
            print_fetch_stdout(summary)
        except Exception as e:
            print(
                f"[fetch] failed: {e}\n"
                "Session replay does not need network. For multi-day, ensure proxy/network "
                "or dump N-day activity JSON under logs/.",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
