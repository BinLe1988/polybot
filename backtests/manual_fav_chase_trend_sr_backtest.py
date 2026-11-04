#!/usr/bin/env python3
"""Favorite-chase R1/R2 + prior-6 trend + $1000 S/R true/false break filters.

Paper only. Does NOT read/print .env secrets; does NOT restart DAXUE/GUIDED.

Filters (both required when enabled; applied BEFORE entry scoring):
  1) --trend3 / --trend6  Prior **3** completed Binance BTCUSDT 5m bars,
     close-to-close monotonic (user update 2026-11-03: lookback is 3, NOT 6).
     CLI keeps --trend6 as alias for enabling the same trend filter; --trend3 preferred.
     Continuous up: close[i] >= close[i-1] for 3 consecutive steps ending at the
     bar that closed at current_window_start (bars [ws-1200, ws-300] → 4 closes).
     Continuous down: symmetric (<=). Up-chase only if 3-up; Down-chase only if 3-down.
  2) --sr1000  Round USD levels every 1000. Skip FALSE breakouts in chase direction;
     allow TRUE breakouts continuing the 6-bar trend; if not near a level → allow
     (no S/R gate). See classify_sr_break() docstring for pierce/reclaim heuristics.

Base rules reused from manual_fav_chase_backtest.py (R1/R2).

Clock / range:
  Asia/Shanghai. --start-sh / --end-sh are INCLUSIVE calendar days
  (same as dump_manual_activity / manual_fav_chase_backtest): end day runs through
  that day's 24:00 SH (= next day 00:00 exclusive).
  Last 3 SH days ending 2026-11-02 EOD:
    --start-sh 2026-10-31 --end-sh 2026-11-02
  (= windows in [2026-10-31 00:00, 2026-11-03 00:00) Asia/Shanghai).

Examples (from /Users/nick/poly-test):
  # Path A fill-tape (reuse week dump or dump 3d first):
  python3 backtests/dump_manual_activity.py \\
    --start-sh 2026-10-31 --end-sh 2026-11-02 --session days3_20261031_1102
  python3 backtests/manual_fav_chase_trend_sr_backtest.py \\
    --session days3_20261031_1102 --rule ALL --trend3 --sr1000 \\
    --start-sh 2026-10-31 --end-sh 2026-11-02

  # Path B mid-sim (denser filter stats):
  python3 backtests/manual_fav_chase_trend_sr_backtest.py \\
    --fetch --start-sh 2026-10-31 --end-sh 2026-11-02 --rule ALL \\
    --trend3 --sr1000 --max-markets 0
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Import base fav-chase helpers (same directory).
BACKTESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKTESTS))
import manual_fav_chase_backtest as base  # noqa: E402

ROOT = BACKTESTS.parent
LOGS = ROOT / "logs"
OUT_DIR = BACKTESTS / "manual_fav_chase"
SH_TZ = base.SH_TZ
WINDOW_SEC = base.WINDOW_SEC
SIZE_USD = base.SIZE_USD
RULES = base.RULES

SR_STEP = 1000.0
# Near-level band: within this USD of a round level counts as "probing".
SR_NEAR_USD = 150.0
# Pierce must exceed level by at least this to count (noise filter).
SR_PIERCE_USD = 20.0
# False break: pierce then reclaim within this many completed 5m bars.
SR_RECLAIM_BARS = 2
# True break: close beyond level and next N bars hold beyond (or last close still beyond).
SR_HOLD_BARS = 1

# Prior completed 5m bars for trend filter (close-to-close monotonic steps).
TREND_N = 3  # user: 3 windows, not 6


# ---------------------------------------------------------------------------
# Binance 5m OHLC
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    open_time: int  # epoch sec (bar open = window start)
    o: float
    h: float
    l: float
    c: float
    v: float


def fetch_binance_5m(start_sec: int, end_sec: int) -> List[Bar]:
    """Public BTCUSDT 5m klines via data-api.binance.vision (proxy-aware env)."""
    out: List[Bar] = []
    cursor_ms = int(start_sec) * 1000
    end_ms = int(end_sec) * 1000
    while cursor_ms < end_ms:
        q = urllib.parse.urlencode(
            {
                "symbol": "BTCUSDT",
                "interval": "5m",
                "startTime": cursor_ms,
                "endTime": end_ms,
                "limit": 1000,
            }
        )
        url = f"https://data-api.binance.vision/api/v3/klines?{q}"
        raw = base.http_get(url)
        if not raw:
            break
        for row in raw:
            ot = int(row[0]) // 1000
            out.append(
                Bar(
                    open_time=ot,
                    o=float(row[1]),
                    h=float(row[2]),
                    l=float(row[3]),
                    c=float(row[4]),
                    v=float(row[5]),
                )
            )
        last_open_ms = int(raw[-1][0])
        nxt = last_open_ms + WINDOW_SEC * 1000
        if nxt <= cursor_ms:
            break
        cursor_ms = nxt
        if len(raw) < 1000:
            break
        time.sleep(0.02)
    # de-dupe by open_time
    by_t = {b.open_time: b for b in out}
    return [by_t[t] for t in sorted(by_t)]


def bars_index(bars: List[Bar]) -> Dict[int, Bar]:
    return {b.open_time: b for b in bars}


# ---------------------------------------------------------------------------
# Filter 1: prior 6×5m continuous trend (close-to-close)
# ---------------------------------------------------------------------------

def prior_trend_closes(idx: Dict[int, Bar], window_start: int, n: int = TREND_N) -> Optional[List[float]]:
    """
    Closes for (n+1) bar opens [ws-(n+1)*300, ..., ws-300] so we can test n
    consecutive close-to-close steps ending at the bar that closed when the
    current market window opened (bar open = ws-300).
    For n=3: bars [ws-1200, ws-900, ws-600, ws-300] → 4 closes, 3 steps.
    """
    closes: List[float] = []
    for k in range(n + 1, 0, -1):
        ot = window_start - k * WINDOW_SEC
        b = idx.get(ot)
        if b is None:
            return None
        closes.append(b.c)
    return closes  # length n+1


def trend_dir(idx: Dict[int, Bar], window_start: int, n: int = TREND_N) -> Tuple[Optional[str], str]:
    """
    Return ('up'|'down'|None, detail).
    Continuous up: each of n steps has close >= prior close (monotonic non-decreasing).
    Continuous down: each step close <= prior close.
    Flat all-equal → None. Mixed → None.
    Tag strings keep 'trend3=' prefix (lookback is TREND_N).
    """
    closes = prior_trend_closes(idx, window_start, n=n)
    tag = f"trend{n}"
    if closes is None:
        return None, f"{tag}=missing_bars"
    ups = all(closes[i] >= closes[i - 1] for i in range(1, n + 1))
    dns = all(closes[i] <= closes[i - 1] for i in range(1, n + 1))
    if ups and dns:
        return None, f"{tag}=flat closes={[round(x,2) for x in closes]}"
    if ups:
        return "up", f"{tag}=up closes={[round(x,2) for x in closes]}"
    if dns:
        return "down", f"{tag}=down closes={[round(x,2) for x in closes]}"
    return None, f"{tag}=mixed closes={[round(x,2) for x in closes]}"


# Back-compat aliases (old names)
def prior6_closes(idx: Dict[int, Bar], window_start: int) -> Optional[List[float]]:
    return prior_trend_closes(idx, window_start, n=TREND_N)


def trend6_dir(idx: Dict[int, Bar], window_start: int) -> Tuple[Optional[str], str]:
    return trend_dir(idx, window_start, n=TREND_N)


def trend6_allows_side(trend: Optional[str], side: str) -> bool:
    return trend_allows_side(trend, side)


def trend_allows_side(trend: Optional[str], side: str) -> bool:
    if trend == "up" and side.lower() == "up":
        return True
    if trend == "down" and side.lower() == "down":
        return True
    return False


# ---------------------------------------------------------------------------
# Filter 2: $1000 S/R true vs false breakout
# ---------------------------------------------------------------------------

def nearest_round_levels(price: float, step: float = SR_STEP) -> List[float]:
    base_lvl = (price // step) * step
    return [base_lvl - step, base_lvl, base_lvl + step, base_lvl + 2 * step]


def classify_sr_break(
    idx: Dict[int, Bar],
    window_start: int,
    side: str,
) -> Tuple[str, str]:
    """
    Classify S/R interaction for the chase side using completed 5m bars before ws.

    Definitions (documented heuristics):
      - Probe window: last 4 completed bars (opens ws-1200 … ws-300).
      - Relevant level only (avoids labeling far-below 1000s as "broken"):
          Up  → resistance R = ceil(max_high / 1000) * 1000 if max_high not on
                a round print, else that print; must be within SR_NEAR_USD of
                the probe range (otherwise none).
          Down→ support S = floor(min_low / 1000) * 1000 similarly.
      - FALSE break (Up): high pierces >= R+SR_PIERCE then close back < R within
        SR_RECLAIM_BARS (wick fakeout / reclaim). Symmetric for Down.
      - TRUE break (Up): a bar closes >= R+SR_PIERCE and subsequent SR_HOLD_BARS
        stay >= R with no later reclaim in the probe. Symmetric for Down.
      - none: level not nearby, or inconclusive pierce without reclaim/hold.

    Gate policy (user intent):
      - skip if classification == 'false' for the chase side
      - allow if 'true' or 'none' (only block clear false breaks)
    """
    import math

    side_l = side.lower()
    bars: List[Bar] = []
    for k in range(4, 0, -1):
        b = idx.get(window_start - k * WINDOW_SEC)
        if b is None:
            return "none", "sr=missing_bars"
        bars.append(b)
    ref = bars[-1].c
    max_h = max(b.h for b in bars)
    min_l = min(b.l for b in bars)

    if side_l == "up":
        # Resistance being challenged from below / broken upward
        R = math.ceil(max_h / SR_STEP) * SR_STEP
        if abs(max_h - R) < 1e-9:
            # Exact touch of round — that level is the one
            pass
        # If ceil jumps far above because max_h just under next round, use that R
        # Require the level to be near the probe range
        if min(abs(max_h - R), abs(ref - R), abs(min_l - R)) > SR_NEAR_USD and not (
            min_l < R < max_h or abs(ref - R) <= SR_NEAR_USD
        ):
            # Also try the round immediately above ref
            R2 = math.ceil(ref / SR_STEP) * SR_STEP
            if R2 != R and (
                min_l < R2 <= max_h + SR_NEAR_USD or abs(ref - R2) <= SR_NEAR_USD
            ):
                R = R2
            else:
                return "none", f"sr=not_near_resistance ref={ref:.2f} max_h={max_h:.2f}"
        L = float(R)
        false_hit = False
        true_hit = False
        for i, b in enumerate(bars):
            pierced = b.h >= L + SR_PIERCE_USD
            closed_above = b.c >= L + SR_PIERCE_USD
            if pierced and not closed_above:
                reclaimed = b.c < L
                for j in range(i + 1, min(i + 1 + SR_RECLAIM_BARS, len(bars))):
                    if bars[j].c < L:
                        reclaimed = True
                if reclaimed:
                    false_hit = True
            if closed_above:
                hold_ok = True
                for j in range(i + 1, min(i + 1 + SR_HOLD_BARS, len(bars))):
                    if bars[j].c < L:
                        hold_ok = False
                        break
                later_reclaim = any(bars[j].c < L for j in range(i + 1, len(bars)))
                if hold_ok and not later_reclaim:
                    true_hit = True
                elif later_reclaim:
                    false_hit = True
        if false_hit and not true_hit:
            return "false", f"sr=FALSE_break_up L={int(L)} ref={ref:.2f}"
        if true_hit:
            return "true", f"sr=TRUE_break_up L={int(L)} ref={ref:.2f}"
        return "none", f"sr=probe_up L={int(L)} ref={ref:.2f} inconclusive"

    # down — support
    S = math.floor(min_l / SR_STEP) * SR_STEP
    if min(abs(min_l - S), abs(ref - S), abs(max_h - S)) > SR_NEAR_USD and not (
        min_l < S < max_h or abs(ref - S) <= SR_NEAR_USD
    ):
        S2 = math.floor(ref / SR_STEP) * SR_STEP
        if S2 != S and (
            min_l - SR_NEAR_USD <= S2 <= max_h or abs(ref - S2) <= SR_NEAR_USD
        ):
            S = S2
        else:
            return "none", f"sr=not_near_support ref={ref:.2f} min_l={min_l:.2f}"
    L = float(S)
    false_hit = False
    true_hit = False
    for i, b in enumerate(bars):
        pierced = b.l <= L - SR_PIERCE_USD
        closed_below = b.c <= L - SR_PIERCE_USD
        if pierced and not closed_below:
            reclaimed = b.c > L
            for j in range(i + 1, min(i + 1 + SR_RECLAIM_BARS, len(bars))):
                if bars[j].c > L:
                    reclaimed = True
            if reclaimed:
                false_hit = True
        if closed_below:
            hold_ok = True
            for j in range(i + 1, min(i + 1 + SR_HOLD_BARS, len(bars))):
                if bars[j].c > L:
                    hold_ok = False
                    break
            later_reclaim = any(bars[j].c > L for j in range(i + 1, len(bars)))
            if hold_ok and not later_reclaim:
                true_hit = True
            elif later_reclaim:
                false_hit = True
    if false_hit and not true_hit:
        return "false", f"sr=FALSE_break_down L={int(L)} ref={ref:.2f}"
    if true_hit:
        return "true", f"sr=TRUE_break_down L={int(L)} ref={ref:.2f}"
    return "none", f"sr=probe_down L={int(L)} ref={ref:.2f} inconclusive"


def filters_allow_entry(
    idx: Dict[int, Bar],
    window_start: int,
    side: str,
    use_trend6: bool,
    use_sr1000: bool,
) -> Tuple[bool, Dict[str, Any]]:
    """Return (ok, debug). Both filters must pass when enabled."""
    info: Dict[str, Any] = {"side": side, "window_start": window_start}
    trend, tdetail = trend_dir(idx, window_start, n=TREND_N)
    info["trend"] = trend
    info["trend_n"] = TREND_N
    info["trend6"] = trend  # compat alias
    info["trend6_detail"] = tdetail
    info["trend_detail"] = tdetail
    if use_trend6:
        if not trend_allows_side(trend, side):
            info["block"] = "trend6_mismatch"  # stable skip token (means trend_n mismatch)
            return False, info

    sr_label, sr_detail = classify_sr_break(idx, window_start, side)
    info["sr"] = sr_label
    info["sr_detail"] = sr_detail
    if use_sr1000:
        if sr_label == "false":
            info["block"] = "sr_false_break"
            return False, info
        # true or none → allow
    info["block"] = None
    return True, info


# ---------------------------------------------------------------------------
# Tape path with filters
# ---------------------------------------------------------------------------

@dataclass
class FilterStats:
    n_baseline_scored: int = 0
    n_filtered_scored: int = 0
    n_blocked_trend6: int = 0
    n_blocked_sr_false: int = 0
    n_blocked_other: int = 0
    n_allowed_sr_true: int = 0
    n_allowed_sr_none: int = 0
    block_examples: List[str] = field(default_factory=list)


def apply_rule_on_tape_with_filters(
    rule: base.RuleSpec,
    rts: List[base.RoundTrip],
    idx: Dict[int, Bar],
    use_trend6: bool,
    use_sr1000: bool,
    filter_on: bool,
) -> Tuple[List[base.SimTrade], FilterStats]:
    """
    Same as base.apply_rule_on_tape but optionally gates entries with trend6+sr.
    When filter_on=False, identical baseline (still records would-block stats).
    """
    duals = base.dual_side_slugs(rts) if rule.one_side_per_window else set()
    candidates = sorted(rts, key=lambda r: (r.entry_ts, r.slug))
    taken_windows: set = set()
    out: List[base.SimTrade] = []
    stats = FilterStats()

    for r in candidates:
        ok, why = base.passes_entry(rule, r.buy_px, float(r.rem_at_entry))
        if not ok:
            continue

        ws = r.window_start or base.window_start_from_slug(r.slug) or 0
        f_ok, f_info = filters_allow_entry(idx, ws, r.outcome, use_trend6, use_sr1000)

        # Always accumulate filter diagnostics for baseline-eligible rows
        # (before dual/one-side skips that aren't about filters).
        would_enter_structure = True
        if rule.one_side_per_window and r.slug in duals:
            would_enter_structure = False
        if rule.one_side_per_window and r.slug in taken_windows:
            would_enter_structure = False

        if would_enter_structure and r.pnl is not None and r.sell_px is not None:
            # count baseline candidate
            pass

        note_extra = ""
        if not f_ok:
            note_extra = f" | FILTER_BLOCK {f_info.get('block')} {f_info.get('trend6_detail')} {f_info.get('sr_detail')}"
            if f_info.get("block") == "trend6_mismatch":
                stats.n_blocked_trend6 += 1
            elif f_info.get("block") == "sr_false_break":
                stats.n_blocked_sr_false += 1
            else:
                stats.n_blocked_other += 1
            if len(stats.block_examples) < 12:
                stats.block_examples.append(
                    f"{r.slug} {r.outcome} {f_info.get('block')} {f_info.get('sr_detail')}"
                )
        else:
            if f_info.get("sr") == "true":
                stats.n_allowed_sr_true += 1
            elif f_info.get("sr") == "none":
                stats.n_allowed_sr_none += 1
            note_extra = f" | FILTER_OK trend={f_info.get('trend6')} sr={f_info.get('sr')}"

        if filter_on and not f_ok:
            out.append(
                base.SimTrade(
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
                    pnl_size5=base.scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode="skipped",
                    note=f"skip_filter{note_extra}",
                )
            )
            continue

        if rule.one_side_per_window and r.slug in duals:
            out.append(
                base.SimTrade(
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
                    pnl_size5=base.scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode="skipped",
                    note="skip_dual_side_window" + note_extra,
                )
            )
            continue
        if rule.one_side_per_window and r.slug in taken_windows:
            out.append(
                base.SimTrade(
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
                    pnl_size5=base.scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode="skipped",
                    note="skip_second_side_or_repeat" + note_extra,
                )
            )
            continue

        taken_windows.add(r.slug)
        if r.pnl is not None and r.sell_px is not None:
            exit_mode = "actual_sell"
            if r.sell_px >= rule.tp_bid:
                exit_mode = "actual_tp099"
            out.append(
                base.SimTrade(
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
                    pnl_size5=base.scale_pnl_to_size(r, rule.size_usd),
                    tag=r.tag,
                    exit_mode=exit_mode,
                    note=why + note_extra,
                )
            )
        else:
            out.append(
                base.SimTrade(
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
                    note="open_on_tape_no_settle_label" + note_extra,
                )
            )
    return out, stats


def filter_stats_from_trades(
    baseline: List[base.SimTrade],
    filtered: List[base.SimTrade],
) -> Dict[str, Any]:
    b_scored = [t for t in baseline if t.exit_mode not in ("skipped", "settle_stub") and t.pnl_actual is not None]
    f_scored = [t for t in filtered if t.exit_mode not in ("skipped", "settle_stub") and t.pnl_actual is not None]
    f_blocked = [t for t in filtered if t.exit_mode == "skipped" and "skip_filter" in (t.note or "")]
    n_trend = sum(1 for t in f_blocked if "trend6_mismatch" in (t.note or ""))
    n_sr = sum(1 for t in f_blocked if "sr_false_break" in (t.note or ""))
    return {
        "n_baseline_scored": len(b_scored),
        "n_filtered_scored": len(f_scored),
        "n_blocked_by_filters": len(f_blocked),
        "n_blocked_trend6": n_trend,
        "n_blocked_sr_false": n_sr,
        "blocked_slugs": [f"{t.slug}:{t.outcome}" for t in f_blocked[:20]],
    }


# ---------------------------------------------------------------------------
# Fetch path with filters
# ---------------------------------------------------------------------------

def sim_rule_on_mids_filtered(
    rule: base.RuleSpec,
    slug: str,
    window_ts: int,
    up_hist: List[dict],
    down_hist: List[dict],
    winner: str,
    idx: Dict[int, Bar],
    use_trend6: bool,
    use_sr1000: bool,
    filter_on: bool,
) -> Optional[base.SimTrade]:
    """Like base.sim_rule_on_mids but skips first trigger if filters fail; tries later triggers."""
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

    entry: Optional[Tuple[int, str, float, str]] = None
    for t, side, mid in series:
        ask = min(0.99, mid + base.HALF_SPREAD)
        rem = base.rem_at(window_ts, t)
        ok, why = base.passes_entry(rule, ask, float(rem))
        if not ok:
            continue
        f_ok, f_info = filters_allow_entry(idx, window_ts, side, use_trend6, use_sr1000)
        if filter_on and not f_ok:
            continue
        note = why
        if filter_on:
            note += f" | FILTER_OK trend={f_info.get('trend6')} sr={f_info.get('sr')}"
        else:
            # baseline: record what filters would say
            note += f" | would_filter={'OK' if f_ok else f_info.get('block')} trend={f_info.get('trend6')} sr={f_info.get('sr')}"
        entry = (t, side, ask, note)
        break
    if entry is None:
        return None

    et, side, buy_px, note = entry
    shares = rule.size_usd / buy_px
    sell_px = None
    exit_ts = None
    for t, s, mid in series:
        if s != side or t <= et:
            continue
        bid = max(0.01, mid - base.HALF_SPREAD)
        if bid >= rule.tp_bid:
            sell_px = rule.tp_bid
            exit_ts = t
            break
    if sell_px is None:
        won = winner == side
        sell_px = 1.0 if won else 0.0
        exit_ts = window_ts + WINDOW_SEC
        tag = "SETTLE_WIN" if won else "SETTLE_LOSS"
        exit_mode = "settle"
    else:
        tag = "WIN_099"
        exit_mode = "sim_tp099"
    fee_in = base.taker_fee(shares, buy_px)
    fee_out = 0.0 if exit_mode == "settle" else base.taker_fee(shares, sell_px)
    pnl = shares * sell_px - rule.size_usd - fee_in - fee_out
    return base.SimTrade(
        rule=rule.name,
        slug=slug,
        outcome=side,
        entry_ts=et,
        exit_ts=exit_ts,
        rem_at_entry=base.rem_at(window_ts, et),
        buy_px=buy_px,
        sell_px=sell_px,
        cost_usdc=rule.size_usd,
        pnl_actual=pnl,
        pnl_size5=pnl,
        tag=tag,
        exit_mode=exit_mode,
        note=note,
    )


def run_fetch_compare(
    rule_names: List[str],
    start_epoch: int,
    end_epoch: int,
    max_markets: int,
    use_trend6: bool,
    use_sr1000: bool,
    trend_windows_only: bool = False,
) -> Dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Need TREND_N+1 bars warmup before first window
    warm_start = start_epoch - (TREND_N + 2) * WINDOW_SEC
    print(f"[klines] fetching Binance 5m {base.ts_to_sh(warm_start)} → {base.ts_to_sh(end_epoch)}", flush=True)
    bars = fetch_binance_5m(warm_start, end_epoch + WINDOW_SEC)
    idx = bars_index(bars)
    print(f"[klines] n_bars={len(bars)}", flush=True)

    windows = base.iter_window_starts(start_epoch, end_epoch)
    trend_prev = {"up": 0, "down": 0, "other": 0}
    for ws in windows:
        d, _ = trend_dir(idx, ws, n=TREND_N)
        if d == "up":
            trend_prev["up"] += 1
        elif d == "down":
            trend_prev["down"] += 1
        else:
            trend_prev["other"] += 1
    n_all = len(windows)
    if trend_windows_only:
        windows = [ws for ws in windows if trend_dir(idx, ws, n=TREND_N)[0] in ("up", "down")]
        print(
            f"[fetch] --trend-windows-only: {n_all} → {len(windows)} "
            f"(up={trend_prev['up']} down={trend_prev['down']} active_pct="
            f"{(trend_prev['up']+trend_prev['down'])/max(1,n_all):.2%})",
            flush=True,
        )
    if max_markets > 0 and len(windows) > max_markets:
        print(f"[fetch] capping windows {len(windows)} → {max_markets}", flush=True)
        windows = windows[:max_markets]
    print(
        f"[fetch] windows={len(windows)} range={base.ts_to_sh(start_epoch)} → {base.ts_to_sh(end_epoch)} "
        f"trend{TREND_N}_prevalence up={trend_prev['up']} down={trend_prev['down']} "
        f"other={trend_prev['other']} pct={(trend_prev['up']+trend_prev['down'])/max(1,n_all):.2%}",
        flush=True,
    )

    results_base: Dict[str, List[base.SimTrade]] = {r: [] for r in rule_names}
    results_filt: Dict[str, List[base.SimTrade]] = {r: [] for r in rule_names}
    block_counts = {r: {"trend6": 0, "sr_false": 0, "total_baseline": 0} for r in rule_names}

    def _process_window(ws: int) -> Optional[Dict[str, Any]]:
        slug = f"btc-updown-5m-{ws}"
        try:
            ev = base.fetch_gamma_event(ws)
        except Exception as e:
            return {"ok": False, "err": f"gamma {slug}: {e}"}
        if not ev or not ev.get("closed"):
            return {"ok": False, "err": "not_closed"}
        mkt = ev["markets"][0]
        outs, prices = base.parse_outcomes(mkt)
        winner, _, _ = base.resolve_winner(outs, prices)
        if winner == "unknown":
            return {"ok": False, "err": "unknown_winner"}
        up_tok, dn_tok = base.parse_tokens(mkt)
        try:
            up_hist = base.fetch_price_history(up_tok, ws - 60, ws + 360)
            dn_hist = base.fetch_price_history(dn_tok, ws - 60, ws + 360)
        except Exception as e:
            return {"ok": False, "err": f"prices {slug}: {e}"}
        out_b: Dict[str, Any] = {}
        out_f: Dict[str, Any] = {}
        for rn in rule_names:
            tr_b = sim_rule_on_mids_filtered(
                RULES[rn], slug, ws, up_hist, dn_hist, winner, idx,
                use_trend6, use_sr1000, filter_on=False,
            )
            tr_f = sim_rule_on_mids_filtered(
                RULES[rn], slug, ws, up_hist, dn_hist, winner, idx,
                use_trend6, use_sr1000, filter_on=True,
            )
            out_b[rn] = tr_b
            out_f[rn] = tr_f
        return {"ok": True, "ws": ws, "base": out_b, "filt": out_f}

    markets_ok = 0
    markets_fail = 0
    workers = 12
    print(f"[fetch] parallel workers={workers}", flush=True)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_process_window, ws): ws for ws in windows}
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                markets_fail += 1
                if markets_fail <= 5:
                    print(f"  warn worker: {e}", flush=True)
                continue
            if not res or not res.get("ok"):
                markets_fail += 1
                if markets_fail <= 5 and res and res.get("err") not in ("not_closed", "unknown_winner"):
                    print(f"  warn {res.get('err')}", flush=True)
                continue
            markets_ok += 1
            for rn in rule_names:
                tr_b = res["base"].get(rn)
                tr_f = res["filt"].get(rn)
                if tr_b:
                    results_base[rn].append(tr_b)
                    block_counts[rn]["total_baseline"] += 1
                    if "would_filter=trend6_mismatch" in (tr_b.note or ""):
                        block_counts[rn]["trend6"] += 1
                    elif "would_filter=sr_false_break" in (tr_b.note or ""):
                        block_counts[rn]["sr_false"] += 1
                if tr_f:
                    results_filt[rn].append(tr_f)
            if done % 40 == 0:
                print(f"  … scanned {done}/{len(windows)} ok={markets_ok}", flush=True)

    tag = (
        datetime.fromtimestamp(start_epoch, tz=SH_TZ).strftime("%Y%m%d")
        + "_"
        + datetime.fromtimestamp(max(start_epoch, end_epoch - 1), tz=SH_TZ).strftime("%Y%m%d")
    )
    summary = {
        "mode": "fetch_multiday_trend_sr",
        "path_label": "B_mid_sim_fetch",
        "start": base.ts_to_sh(start_epoch),
        "end": base.ts_to_sh(end_epoch),
        "start_sh_inclusive": datetime.fromtimestamp(start_epoch, tz=SH_TZ).strftime("%Y-%m-%d"),
        "end_sh_inclusive": datetime.fromtimestamp(max(start_epoch, end_epoch - 1), tz=SH_TZ).strftime("%Y-%m-%d"),
        "range_note": "end epoch is exclusive; --end-sh is inclusive Shanghai calendar day",
        "markets_ok": markets_ok,
        "markets_fail_or_skip": markets_fail,
        "filters": {
            "trend_n": TREND_N,
            "trend6": use_trend6,
            "trend3": use_trend6,
            "sr1000": use_sr1000,
            "trend_windows_only": trend_windows_only,
            "trend_prevalence": {
                **trend_prev,
                "n_windows_in_range": n_all,
                "active_pct": round((trend_prev["up"] + trend_prev["down"]) / max(1, n_all), 4),
            },
            "trend6_def": f"ALIAS: prior {TREND_N} completed 5m close-to-close monotonic; Up chase only if up; Down only if down",
            "trend3_def": f"prior {TREND_N} completed 5m close-to-close monotonic (need {TREND_N+1} closes); Up chase only if up; Down only if down",
            "sr1000_def": (
                f"levels every {int(SR_STEP)}; near={SR_NEAR_USD}; pierce={SR_PIERCE_USD}; "
                f"false=pierce+reclaim within {SR_RECLAIM_BARS} bars; "
                f"true=close beyond+hold {SR_HOLD_BARS}; gate=skip false only"
            ),
            "klines_n": len(bars),
            "klines_source": "data-api.binance.vision BTCUSDT 5m",
        },
        "rules_baseline": {rn: base.summarize_trades(results_base[rn], rn) for rn in rule_names},
        "rules_filtered": {rn: base.summarize_trades(results_filt[rn], rn) for rn in rule_names},
        "filter_block_stats_on_baseline_triggers": block_counts,
        "definitions": FILTER_DEFS,
        "caveats": CAVEATS_EXTRA + base.CAVEATS,
        "generated_at_asia_shanghai": datetime.now(SH_TZ).isoformat(),
    }
    out_path = OUT_DIR / f"trend_sr_fetch_summary_{tag}.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    # also latest pointer
    (OUT_DIR / "trend_sr_fetch_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # trades csv
    fields = [
        "variant", "rule", "slug", "outcome", "entry_ts", "exit_ts", "rem_at_entry",
        "buy_px", "sell_px", "cost_usdc", "pnl_actual", "pnl_size5", "tag", "exit_mode", "note",
    ]
    trades_path = OUT_DIR / f"trend_sr_fetch_trades_{tag}.csv"
    with open(trades_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for rn in rule_names:
            for t in results_base[rn]:
                row = asdict(t)
                row["variant"] = "baseline"
                w.writerow({k: row.get(k) for k in fields})
            for t in results_filt[rn]:
                row = asdict(t)
                row["variant"] = "filtered"
                w.writerow({k: row.get(k) for k in fields})
    print(f"Wrote {out_path}")
    print(f"Wrote {trades_path}")
    return summary


# ---------------------------------------------------------------------------
# Session path with filters + date slice
# ---------------------------------------------------------------------------

def run_session_compare(
    session_id: str,
    rule_names: List[str],
    start_epoch: Optional[int],
    end_epoch: Optional[int],
    use_trend6: bool,
    use_sr1000: bool,
) -> Dict[str, Any]:
    activity_path, _, _ = base.load_session_files(session_id)
    # Also accept copy under OUT_DIR
    if not activity_path.exists():
        alt = OUT_DIR / f"manual_activity_{session_id}.json"
        if alt.exists():
            activity_path = alt
    print(f"[session] activity={activity_path}", flush=True)

    fills = base.fills_from_activity(activity_path, start_epoch, end_epoch)
    rts = base.fifo_roundtrips(fills)
    closed = [r for r in rts if r.pnl is not None]
    print(
        f"[session] fills={len(fills)} roundtrips={len(rts)} closed={len(closed)} "
        f"closed_pnl={sum(r.pnl or 0 for r in closed):.4f}",
        flush=True,
    )

    if not fills and not rts:
        print("[session] WARNING: no fills in range", flush=True)

    # Determine klines span from tape + warmup
    if start_epoch is None or end_epoch is None:
        meta = json.loads(activity_path.read_text(encoding="utf-8")).get("meta") or {}
        start_epoch = start_epoch or int(meta.get("win_start_epoch") or 0)
        end_epoch = end_epoch or int(meta.get("win_end_epoch") or 0)
    warm_start = int(start_epoch) - (TREND_N + 2) * WINDOW_SEC
    print(f"[klines] fetching Binance 5m {base.ts_to_sh(warm_start)} → {base.ts_to_sh(end_epoch)}", flush=True)
    bars = fetch_binance_5m(warm_start, int(end_epoch) + WINDOW_SEC)
    idx = bars_index(bars)
    print(f"[klines] n_bars={len(bars)}", flush=True)

    rule_results: Dict[str, Any] = {}
    all_sim: List[Dict[str, Any]] = []
    for rn in rule_names:
        sim_b, _ = apply_rule_on_tape_with_filters(
            RULES[rn], rts, idx, use_trend6, use_sr1000, filter_on=False
        )
        sim_f, _ = apply_rule_on_tape_with_filters(
            RULES[rn], rts, idx, use_trend6, use_sr1000, filter_on=True
        )
        # For baseline with filter_on=False, filter skips are NOT applied —
        # but apply_rule still tags notes. Recompute baseline via base for clean headline.
        sim_b_clean = base.apply_rule_on_tape(RULES[rn], rts)
        # Date-slice: base doesn't know date; fills already sliced so OK.
        fstats = filter_stats_from_trades(sim_b_clean, sim_f)
        rule_results[rn] = {
            "baseline": base.summarize_trades(sim_b_clean, rn),
            "filtered": base.summarize_trades(sim_f, rn),
            "filter_stats": fstats,
        }
        for t in sim_b_clean:
            row = asdict(t)
            row["variant"] = "baseline"
            all_sim.append(row)
        for t in sim_f:
            row = asdict(t)
            row["variant"] = "filtered"
            all_sim.append(row)

    tag = session_id
    if start_epoch and end_epoch:
        tag = (
            datetime.fromtimestamp(start_epoch, tz=SH_TZ).strftime("%Y%m%d")
            + "_"
            + datetime.fromtimestamp(max(start_epoch, end_epoch - 1), tz=SH_TZ).strftime("%Y%m%d")
        )
    payload = {
        "mode": "session_replay_trend_sr",
        "path_label": "A_fill_tape",
        "session_id": session_id,
        "start": base.ts_to_sh(start_epoch) if start_epoch else None,
        "end": base.ts_to_sh(end_epoch) if end_epoch else None,
        "generated_at_asia_shanghai": datetime.now(SH_TZ).isoformat(),
        "size_usd_paper": SIZE_USD,
        "filters": {
            "trend_n": TREND_N,
            "trend6": use_trend6,
            "trend3": use_trend6,
            "sr1000": use_sr1000,
            "klines_n": len(bars),
            "klines_source": "data-api.binance.vision BTCUSDT 5m",
        },
        "tape": {
            "n_fills": len(fills),
            "n_roundtrips": len(rts),
            "n_closed": len(closed),
            "closed_pnl": round(sum(r.pnl or 0 for r in closed), 4),
        },
        "rule_results": rule_results,
        "definitions": FILTER_DEFS,
        "caveats": CAVEATS_EXTRA + base.CAVEATS,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / f"trend_sr_session_{tag}_summary.json"
    out_csv = OUT_DIR / f"trend_sr_session_{tag}_trades.csv"
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "trend_sr_session_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    fields = ["variant"] + (
        list(all_sim[0].keys()) if all_sim else [
            "rule", "slug", "outcome", "entry_ts", "exit_ts", "rem_at_entry",
            "buy_px", "sell_px", "cost_usdc", "pnl_actual", "pnl_size5", "tag", "exit_mode", "note",
        ]
    )
    # ensure variant first unique
    fields = ["variant"] + [k for k in fields if k != "variant"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in all_sim:
            w.writerow(row)
    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    return payload


FILTER_DEFS = {
    "trend3": (
        "Relative to current 5m market window start ws: take Binance BTCUSDT 5m closes "
        "for bar opens [ws-1200, ws-900, ws-600, ws-300] (4 closes). Continuous UP = each of "
        "the 3 consecutive close-to-close steps has close[i] >= close[i-1]. Continuous DOWN "
        "symmetric (<=). Flat (all equal) = no trend. Entry: Up-chase only if UP; "
        "Down-chase only if DOWN; otherwise block. "
        "CLI: --trend3 (preferred) or --trend6 (alias enabling same filter); TREND_N=3."
    ),
    "trend6": (
        "ALIAS of trend3: lookback is 3 completed 5m windows (NOT 6). See definitions.trend3."
    ),
    "sr1000": (
        f"Round levels every {int(SR_STEP)} USD. Probe last 4 completed 5m bars. "
        f"Near-level band ±{SR_NEAR_USD}. Pierce beyond level by ≥{SR_PIERCE_USD}. "
        f"FALSE break: pierce then reclaim (close back through level) within {SR_RECLAIM_BARS} bars "
        f"(or close+later reclaim). TRUE break: close beyond level and hold "
        f"(subsequent {SR_HOLD_BARS} bar(s) stay beyond; no later reclaim in probe). "
        "Gate: SKIP only false breaks on the chase side; ALLOW true breaks and non-near-level (none)."
    ),
    "base_rules": {
        "R1": "BUY fill/ask>=0.65, rem in (120,240]; SELL bid>=0.99 else hold-settle; $5; one side/window",
        "R2": "BUY fill>=0.80; TP 0.99; skip rem<60; $5; one side/window",
    },
}

CAVEATS_EXTRA = [
    "Filters use Binance BTCUSDT 5m OHLC, not Polymarket window open/settle prints — small timestamp/basis mismatch possible.",
    "S/R true/false heuristics are rule-based (pierce/reclaim/hold); not ML; borderline probes labeled 'none' and allowed.",
    "Path A fill-tape is funder wallet activity (may include bot fills), not pure manual fav-chase labels.",
    "Path B mid±0.005 sim cannot prove click timing / depth; sparse CLOB history may miss or skew triggers.",
    "Both filters must pass when --trend6 and --sr1000 are set; compare baseline vs filtered in the same run.",
]


def print_compare(payload: Dict[str, Any], kind: str) -> None:
    print(f"\n======== fav_chase trend+SR ({kind}) ========")
    print(f"range: {payload.get('start')} → {payload.get('end')}")
    filt = payload.get("filters") or {}
    print(f"filters: trend6={filt.get('trend6')} sr1000={filt.get('sr1000')} klines={filt.get('klines_n')}")
    if kind.startswith("session"):
        for rn, block in (payload.get("rule_results") or {}).items():
            b = block["baseline"]
            f = block["filtered"]
            fs = block["filter_stats"]
            print(
                f"{rn} BASELINE: n={b['n_scored']} WR={b['wr']} pnl_actual=${b['pnl_actual_sum']} pnl5=${b['pnl_size5_sum']}"
            )
            print(
                f"{rn} FILTERED: n={f['n_scored']} WR={f['wr']} pnl_actual=${f['pnl_actual_sum']} pnl5=${f['pnl_size5_sum']}"
            )
            print(
                f"{rn} blocked: total={fs['n_blocked_by_filters']} trend6={fs['n_blocked_trend6']} "
                f"sr_false={fs['n_blocked_sr_false']}"
            )
    else:
        for rn in (payload.get("rules_baseline") or {}):
            b = payload["rules_baseline"][rn]
            f = payload["rules_filtered"][rn]
            bc = (payload.get("filter_block_stats_on_baseline_triggers") or {}).get(rn, {})
            print(
                f"{rn} BASELINE: n={b['n_scored']} WR={b['wr']} pnl=${b['pnl_actual_sum']}"
            )
            print(
                f"{rn} FILTERED: n={f['n_scored']} WR={f['wr']} pnl=${f['pnl_actual_sum']}"
            )
            print(
                f"{rn} baseline_triggers={bc.get('total_baseline')} would_block_trend6={bc.get('trend6')} "
                f"would_block_sr_false={bc.get('sr_false')}"
            )
    print("trend3 def:", FILTER_DEFS.get("trend3", FILTER_DEFS["trend6"])[:140], "...")
    print("sr1000 def:", FILTER_DEFS["sr1000"][:120], "...")
    print("================================================\n")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fav-chase R1/R2 + trend6 + sr1000 backtest")
    p.add_argument("--session", type=str, default=None, help="Activity session id under logs/")
    p.add_argument("--rule", type=str, default="ALL", help="R1 | R2 | ALL")
    p.add_argument("--fetch", action="store_true", help="Path B mid-sim via Gamma+CLOB")
    p.add_argument("--start-sh", type=str, default=None, help="YYYY-MM-DD Asia/Shanghai inclusive")
    p.add_argument("--end-sh", type=str, default=None, help="YYYY-MM-DD Asia/Shanghai inclusive")
    p.add_argument("--max-markets", type=int, default=96, help="Cap windows for --fetch (0=no cap)")
    p.add_argument("--trend3", action="store_true", help="Enable prior-3 continuous trend filter (close-to-close)")
    p.add_argument("--trend6", action="store_true", help="Alias of --trend3 (lookback is 3, not 6)")
    p.add_argument("--sr1000", action="store_true", help="Enable $1000 S/R true/false break gate")
    p.add_argument(
        "--trend-windows-only",
        action="store_true",
        help="With --fetch: only simulate windows where trend3 is up or down (fast path B)",
    )
    p.add_argument(
        "--require-both-filters",
        action="store_true",
        default=True,
        help=argparse.SUPPRESS,
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    rule_arg = (args.rule or "ALL").upper()
    if rule_arg == "ALL":
        rule_names = ["R1", "R2"]
    elif rule_arg in RULES:
        rule_names = [rule_arg]
    else:
        print(f"Unknown --rule {args.rule}", file=sys.stderr)
        return 2

    use_trend6 = bool(getattr(args, "trend3", False) or args.trend6)
    use_sr1000 = bool(args.sr1000)
    if not use_trend6 and not use_sr1000:
        print("[info] neither --trend3/--trend6 nor --sr1000 set; enabling BOTH", flush=True)
        use_trend6 = True
        use_sr1000 = True
    print(f"[filters] trend_n={TREND_N} enabled={use_trend6} sr1000={use_sr1000} "
          f"(--trend6 is alias; lookback is {TREND_N})", flush=True)

    start_e = end_e = None
    if args.start_sh and args.end_sh:
        start_e, end_e = base.sh_day_range(args.start_sh, args.end_sh)
        print(
            f"[range] inclusive SH days {args.start_sh}→{args.end_sh} "
            f"epochs=[{start_e},{end_e}) = {base.ts_to_sh(start_e)} → {base.ts_to_sh(end_e)}",
            flush=True,
        )

    if not args.session and not args.fetch:
        print("Specify --session and/or --fetch", file=sys.stderr)
        return 2

    rc = 0
    if args.session:
        try:
            payload = run_session_compare(
                args.session, rule_names, start_e, end_e, use_trend6, use_sr1000
            )
            print_compare(payload, "session_A_fill_tape")
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            rc = 1

    if args.fetch:
        if start_e is None or end_e is None:
            # default last 3 SH calendar days through yesterday EOD relative to "today"
            now_sh = datetime.now(SH_TZ)
            end_day = (now_sh - timedelta(days=1)).strftime("%Y-%m-%d")
            start_day = (now_sh - timedelta(days=3)).strftime("%Y-%m-%d")
            start_e, end_e = base.sh_day_range(start_day, end_day)
            print(f"[fetch] defaulting to {start_day}→{end_day} inclusive", flush=True)
        try:
            summary = run_fetch_compare(
                rule_names, start_e, end_e, args.max_markets, use_trend6, use_sr1000,
                trend_windows_only=bool(getattr(args, "trend_windows_only", False)),
            )
            print_compare(summary, "fetch_B_mid_sim")
        except Exception as e:
            print(f"[fetch] failed: {e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
