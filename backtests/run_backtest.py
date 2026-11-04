#!/usr/bin/env python3
"""Paper backtest: Polymarket BTC 5m Up/Down + Up+Down mechanical arb.

NO LIVE ORDERS. Public market data only.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

for k, v in {
    "HTTPS_PROXY": "http://127.0.0.1:7890",
    "HTTP_PROXY": "http://127.0.0.1:7890",
    "ALL_PROXY": "http://127.0.0.1:7890",
}.items():
    os.environ.setdefault(k, v)

OUT_DIR = Path(__file__).resolve().parent
MAX_NOTIONAL = 10.0
FEE_RATE = 0.07
TARGET_MARKETS = 200
ENTRY_DELAY_SEC = 15
HALF_SPREAD_ASK = 0.005
ASK_TICK = 0.01
SH_TZ = timezone(timedelta(hours=8))


def http_get(url: str, timeout: float = 30.0, retries: int = 4) -> Any:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "poly-test-paper-backtest/1.0", "Accept": "application/json"},
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


@dataclass
class MarketRow:
    slug: str
    title: str
    window_ts: int
    closed: bool
    volume: float
    up_token: str
    down_token: str
    condition_id: str
    winner: str
    up_final: float
    down_final: float
    entry_up_mid: Optional[float]
    entry_down_mid: Optional[float]
    min_mid_sum: Optional[float]
    min_ask_sum_hs: Optional[float]
    min_ask_sum_tick: Optional[float]
    n_price_pts: int
    skip_reason: str = ""


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


def entry_mids(
    up_hist: List[dict], down_hist: List[dict], window_ts: int
) -> Tuple[Optional[float], Optional[float]]:
    target = window_ts + ENTRY_DELAY_SEC

    def nearest(hist: List[dict]) -> Optional[float]:
        if not hist:
            return None
        after = [h for h in hist if h["t"] >= window_ts]
        pool = after if after else hist
        best = min(pool, key=lambda h: abs(h["t"] - target))
        return float(best["p"])

    return nearest(up_hist), nearest(down_hist)


def align_and_sum_stats(
    up_hist: List[dict], down_hist: List[dict], window_ts: int
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if not up_hist or not down_hist:
        return None, None, None
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

    mid_sums, ask_hs, ask_tick = [], [], []
    for h in up_hist:
        t = h["t"]
        if t < window_ts or t > window_ts + 300:
            continue
        up_p = float(h["p"])
        dn_p = nearest_down(t)
        if dn_p is None:
            continue
        mid_sums.append(up_p + dn_p)
        ask_hs.append(min(0.99, up_p + HALF_SPREAD_ASK) + min(0.99, dn_p + HALF_SPREAD_ASK))
        ask_tick.append(min(0.99, up_p + ASK_TICK) + min(0.99, dn_p + ASK_TICK))
    if not mid_sums:
        return None, None, None
    return min(mid_sums), min(ask_hs), min(ask_tick)


def collect_markets(n_target: int) -> List[MarketRow]:
    now = int(time.time())
    win = now - (now % 300)
    rows: List[MarketRow] = []
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
            time.sleep(0.05)
            dn_hist = fetch_price_history(dn_tok, ts - 120, ts + 360)
            time.sleep(0.05)
        except Exception as ex:
            print(f"  warn prices {ts}: {ex}", flush=True)
            up_hist, dn_hist = [], []

        eu, ed = entry_mids(up_hist, dn_hist, ts)
        min_mid, min_ask_hs, min_ask_tk = align_and_sum_stats(up_hist, dn_hist, ts)

        row = MarketRow(
            slug=e.get("slug") or m.get("slug"),
            title=e.get("title") or m.get("question") or "",
            window_ts=ts,
            closed=True,
            volume=float(e.get("volume") or m.get("volume") or 0),
            up_token=up_tok,
            down_token=dn_tok,
            condition_id=m.get("conditionId") or "",
            winner=winner,
            up_final=up_f,
            down_final=dn_f,
            entry_up_mid=eu,
            entry_down_mid=ed,
            min_mid_sum=min_mid,
            min_ask_sum_hs=min_ask_hs,
            min_ask_sum_tick=min_ask_tk,
            n_price_pts=len(up_hist) + len(dn_hist),
            skip_reason="" if (eu is not None and ed is not None) else "no_entry_mids",
        )
        rows.append(row)
        if len(rows) % 25 == 0:
            print(f"  collected {len(rows)} ... last={row.slug} winner={winner}", flush=True)

    print(f"Done collecting: {len(rows)} markets", flush=True)
    return rows


@dataclass
class TradeResult:
    slug: str
    strategy: str
    side: str
    entry_price: float
    shares: float
    notional: float
    fee: float
    winner: str
    won: bool
    pnl_gross: float
    pnl_net: float


def buy_shares_for_notional(price: float, notional: float) -> Tuple[float, float, float]:
    p = max(0.01, min(0.99, float(price)))
    denom = 1.0 + FEE_RATE * (1.0 - p)
    spend = notional / denom
    shares = spend / p
    fee = taker_fee(shares, p)
    return shares, spend, fee


def pnl_binary(shares: float, spend: float, fee: float, won: bool) -> Tuple[float, float]:
    payout = shares * 1.0 if won else 0.0
    gross = payout - spend
    net = payout - spend - fee
    return gross, net


def run_directional(rows: List[MarketRow]) -> Dict[str, List[TradeResult]]:
    strategies = {
        "naive_momentum": [],
        "fade_favorite": [],
        "always_up": [],
        "always_down": [],
    }
    for r in rows:
        if r.entry_up_mid is None or r.entry_down_mid is None:
            continue
        up, dn = r.entry_up_mid, r.entry_down_mid
        winner = r.winner

        if up > 0.55 and up >= dn:
            sh, sp, fee = buy_shares_for_notional(up, MAX_NOTIONAL)
            won = winner == "Up"
            g, n = pnl_binary(sh, sp, fee, won)
            strategies["naive_momentum"].append(
                TradeResult(r.slug, "naive_momentum", "Up", up, sh, sp, fee, winner, won, g, n)
            )
        elif dn > 0.55 and dn > up:
            sh, sp, fee = buy_shares_for_notional(dn, MAX_NOTIONAL)
            won = winner == "Down"
            g, n = pnl_binary(sh, sp, fee, won)
            strategies["naive_momentum"].append(
                TradeResult(r.slug, "naive_momentum", "Down", dn, sh, sp, fee, winner, won, g, n)
            )

        if up < 0.45 and dn > 0.55:
            sh, sp, fee = buy_shares_for_notional(up, MAX_NOTIONAL)
            won = winner == "Up"
            g, n = pnl_binary(sh, sp, fee, won)
            strategies["fade_favorite"].append(
                TradeResult(r.slug, "fade_favorite", "Up", up, sh, sp, fee, winner, won, g, n)
            )
        elif dn < 0.45 and up > 0.55:
            sh, sp, fee = buy_shares_for_notional(dn, MAX_NOTIONAL)
            won = winner == "Down"
            g, n = pnl_binary(sh, sp, fee, won)
            strategies["fade_favorite"].append(
                TradeResult(r.slug, "fade_favorite", "Down", dn, sh, sp, fee, winner, won, g, n)
            )

        sh, sp, fee = buy_shares_for_notional(up, MAX_NOTIONAL)
        won = winner == "Up"
        g, n = pnl_binary(sh, sp, fee, won)
        strategies["always_up"].append(
            TradeResult(r.slug, "always_up", "Up", up, sh, sp, fee, winner, won, g, n)
        )
        sh, sp, fee = buy_shares_for_notional(dn, MAX_NOTIONAL)
        won = winner == "Down"
        g, n = pnl_binary(sh, sp, fee, won)
        strategies["always_down"].append(
            TradeResult(r.slug, "always_down", "Down", dn, sh, sp, fee, winner, won, g, n)
        )

    return strategies


@dataclass
class ArbOpp:
    slug: str
    price_up: float
    price_down: float
    sum_px: float
    edge_gross: float
    fee_total: float
    edge_net: float
    shares: float
    pnl_gross: float
    pnl_net: float
    mode: str
    fee_buffer: float


def run_arb(rows: List[MarketRow]) -> Dict[str, Any]:
    results: Dict[str, List[ArbOpp]] = {}
    buffers = [0.02, 0.01]
    modes = [
        ("entry_mid", "entry"),
        ("min_mid", "min_mid"),
        ("min_ask_hs", "ask_hs"),
        ("min_ask_tick", "ask_tick"),
    ]

    for buf in buffers:
        for mode_name, kind in modes:
            key = f"arb_{mode_name}_buf{buf}"
            opps: List[ArbOpp] = []
            for r in rows:
                if kind == "entry":
                    if r.entry_up_mid is None or r.entry_down_mid is None:
                        continue
                    pu, pd = r.entry_up_mid, r.entry_down_mid
                elif kind == "min_mid":
                    if r.min_mid_sum is None or r.entry_up_mid is None or r.entry_down_mid is None:
                        continue
                    s0 = r.entry_up_mid + r.entry_down_mid
                    if s0 <= 0:
                        continue
                    scale = r.min_mid_sum / s0
                    pu, pd = r.entry_up_mid * scale, r.entry_down_mid * scale
                elif kind == "ask_hs":
                    if r.min_ask_sum_hs is None or r.entry_up_mid is None or r.entry_down_mid is None:
                        continue
                    s0 = (r.entry_up_mid + HALF_SPREAD_ASK) + (r.entry_down_mid + HALF_SPREAD_ASK)
                    if s0 <= 0:
                        continue
                    scale = r.min_ask_sum_hs / s0
                    pu = min(0.99, (r.entry_up_mid + HALF_SPREAD_ASK) * scale)
                    pd = min(0.99, (r.entry_down_mid + HALF_SPREAD_ASK) * scale)
                else:
                    if r.min_ask_sum_tick is None or r.entry_up_mid is None or r.entry_down_mid is None:
                        continue
                    s0 = (r.entry_up_mid + ASK_TICK) + (r.entry_down_mid + ASK_TICK)
                    if s0 <= 0:
                        continue
                    scale = r.min_ask_sum_tick / s0
                    pu = min(0.99, (r.entry_up_mid + ASK_TICK) * scale)
                    pd = min(0.99, (r.entry_down_mid + ASK_TICK) * scale)

                s = pu + pd
                if s >= 1.0 - buf:
                    continue
                C = MAX_NOTIONAL / s
                spend = C * s
                fee = taker_fee(C, pu) + taker_fee(C, pd)
                payout = C
                pnl_g = payout - spend
                pnl_n = payout - spend - fee
                edge_g = 1.0 - s
                edge_n = edge_g - (fee / C)
                opps.append(
                    ArbOpp(
                        slug=r.slug,
                        price_up=pu,
                        price_down=pd,
                        sum_px=s,
                        edge_gross=edge_g,
                        fee_total=fee,
                        edge_net=edge_n,
                        shares=C,
                        pnl_gross=pnl_g,
                        pnl_net=pnl_n,
                        mode=mode_name,
                        fee_buffer=buf,
                    )
                )
            results[key] = opps
    return results


def summarize_trades(name: str, trades: List[TradeResult]) -> dict:
    if not trades:
        return {
            "strategy": name,
            "trades": 0,
            "win_rate": None,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "total_fees": 0.0,
            "max_drawdown": 0.0,
            "avg_pnl_net": None,
            "total_notional": 0.0,
        }
    wins = sum(1 for t in trades if t.won)
    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.pnl_net)
    return {
        "strategy": name,
        "trades": len(trades),
        "wins": wins,
        "win_rate": wins / len(trades),
        "net_pnl": sum(t.pnl_net for t in trades),
        "gross_pnl": sum(t.pnl_gross for t in trades),
        "total_fees": sum(t.fee for t in trades),
        "max_drawdown": max_drawdown(equity),
        "avg_pnl_net": sum(t.pnl_net for t in trades) / len(trades),
        "total_notional": sum(t.notional for t in trades),
    }


def summarize_arb(name: str, opps: List[ArbOpp], n_markets: int) -> dict:
    if not opps:
        return {
            "strategy": name,
            "opportunities": 0,
            "opportunity_rate": 0.0,
            "avg_edge_gross": None,
            "avg_edge_net": None,
            "net_pnl": 0.0,
            "gross_pnl": 0.0,
            "total_fees": 0.0,
            "max_drawdown": 0.0,
            "n_markets": n_markets,
        }
    equity = [0.0]
    for o in opps:
        equity.append(equity[-1] + o.pnl_net)
    return {
        "strategy": name,
        "opportunities": len(opps),
        "opportunity_rate": len(opps) / n_markets if n_markets else 0.0,
        "avg_edge_gross": sum(o.edge_gross for o in opps) / len(opps),
        "avg_edge_net": sum(o.edge_net for o in opps) / len(opps),
        "avg_sum_px": sum(o.sum_px for o in opps) / len(opps),
        "net_pnl": sum(o.pnl_net for o in opps),
        "gross_pnl": sum(o.pnl_gross for o in opps),
        "total_fees": sum(o.fee_total for o in opps),
        "max_drawdown": max_drawdown(equity),
        "n_markets": n_markets,
    }


def ts_to_sh(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=SH_TZ).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")


def write_report(
    rows: List[MarketRow],
    dir_summaries: Dict[str, dict],
    arb_summaries: Dict[str, dict],
) -> None:
    usable = [r for r in rows if r.entry_up_mid is not None]
    ts_min = min(r.window_ts for r in rows)
    ts_max = max(r.window_ts for r in rows)
    up_wins = sum(1 for r in rows if r.winner == "Up")
    dn_wins = sum(1 for r in rows if r.winner == "Down")

    lines = []
    lines.append("# Polymarket BTC 5-minute Up/Down — Paper Backtest Report")
    lines.append("")
    lines.append("**Mode:** PAPER ONLY — no live orders were placed.")
    lines.append("**Profits are NOT guaranteed.** Past paper results do not predict future live results.")
    lines.append("")
    lines.append("## Sample")
    lines.append(f"- Markets collected: **{len(rows)}** closed `btc-updown-5m-*` windows")
    lines.append(f"- With entry mid prices: **{len(usable)}**")
    lines.append(f"- Date range (window start): **{ts_to_sh(ts_min)}** → **{ts_to_sh(ts_max)}**")
    lines.append(f"- Outcomes in sample: Up wins={up_wins}, Down wins={dn_wins}")
    lines.append(f"- Max notional per trade: **${MAX_NOTIONAL:.0f}** (matches user live limit)")
    lines.append("")
    lines.append("## Methodology")
    lines.append("1. Enumerate recent closed events via `gamma-api.polymarket.com/events?slug=btc-updown-5m-{unix}`.")
    lines.append("2. Resolution from `outcomePrices` (Up/Down → 1/0).")
    lines.append("3. Entry signal: mid from CLOB `/prices-history` (~15s after window open, fidelity=1).")
    lines.append("4. Directional sizing: spend ≈ $10 notional (fee-adjusted) as **taker**.")
    lines.append(
        f"5. Fees: crypto taker `fee = shares × {FEE_RATE} × p × (1−p)` "
        "(Polymarket docs; peaks ~3.5% of notional near p=0.50)."
    )
    lines.append("6. Winning shares pay $1 at resolution; losers $0.")
    lines.append("7. **Arb:** buy BOTH Up and Down with equal shares when estimated ask_up+ask_down < 1−fee_buffer.")
    lines.append("   - `entry_mid`: use entry mids as fill prices (optimistic).")
    lines.append("   - `min_mid`: min aligned mid_up+mid_down during the 5m window (optimistic).")
    lines.append(f"   - `min_ask_hs`: ask≈mid+{HALF_SPREAD_ASK} (half-tick; still optimistic vs real book).")
    lines.append(f"   - `min_ask_tick`: ask≈mid+{ASK_TICK} (more conservative).")
    lines.append("   - Fillability assumption: can take both displayed prices up to $10 total notional.")
    lines.append("   - **No historical L2 book** was available for closed markets; arb edges are approximated.")
    lines.append("")
    lines.append("## Directional strategies")
    lines.append("")
    lines.append("| Strategy | Trades | Win rate | Gross PnL | Fees | Net PnL | Max DD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for k in ["naive_momentum", "fade_favorite", "always_up", "always_down"]:
        s = dir_summaries[k]
        wr = f"{100*s['win_rate']:.1f}%" if s["win_rate"] is not None else "n/a"
        lines.append(
            f"| {k} | {s['trades']} | {wr} | ${s['gross_pnl']:.2f} | ${s['total_fees']:.2f} | "
            f"${s['net_pnl']:.2f} | ${s['max_drawdown']:.2f} |"
        )
    lines.append("")
    lines.append("### Strategy definitions")
    lines.append("- **naive_momentum**: if Up mid > 0.55 (and ≥ Down) buy Up; elif Down mid > 0.55 buy Down; else skip.")
    lines.append("- **fade_favorite**: if one side mid < 0.45 and the other > 0.55, buy the cheap side.")
    lines.append("- **always_up / always_down**: baseline — buy that side every market at entry mid.")
    lines.append("")
    lines.append("## Up+Down mechanical arb")
    lines.append("")
    lines.append("| Variant | #Opps | Opp rate | Avg edge gross | Avg edge net | Gross PnL | Net PnL | Max DD |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, s in sorted(arb_summaries.items()):
        ag = f"{100*s['avg_edge_gross']:.2f}%" if s["avg_edge_gross"] is not None else "n/a"
        an = f"{100*s['avg_edge_net']:.2f}%" if s["avg_edge_net"] is not None else "n/a"
        lines.append(
            f"| {k} | {s['opportunities']} | {100*s['opportunity_rate']:.1f}% | {ag} | {an} | "
            f"${s['gross_pnl']:.2f} | ${s['net_pnl']:.2f} | ${s['max_drawdown']:.2f} |"
        )
    lines.append("")
    lines.append("## Recommendation")
    ranked = sorted(
        [s for s in dir_summaries.values() if s["trades"] > 0],
        key=lambda x: x["net_pnl"],
        reverse=True,
    )
    best_arb = sorted(arb_summaries.values(), key=lambda x: x["net_pnl"], reverse=True)[0]

    lines.append("")
    if ranked:
        best = ranked[0]
        lines.append(
            f"- Best directional **in this paper sample**: `{best['strategy']}` "
            f"with net PnL **${best['net_pnl']:.2f}** over {best['trades']} trades "
            f"(win rate {100*best['win_rate']:.1f}%, max DD ${best['max_drawdown']:.2f})."
        )
    lines.append(
        f"- Best arb variant **in this paper sample**: `{best_arb['strategy']}` "
        f"net PnL **${best_arb['net_pnl']:.2f}** over {best_arb['opportunities']} opportunities."
    )
    lines.append("")
    lines.append("### Live vs stay paper")
    lines.append(
        "- **Stay paper / do not scale live** on naive momentum or fade-favorite as a standalone edge: "
        "crypto taker fees (~1–3.5% of notional) are large vs typical mid-price signal edge on 5m windows; "
        "win rates near coin-flip after fees usually lose."
    )
    lines.append(
        "- **Up+Down arb** is the only mechanically-sound idea, but this backtest **lacks true historical asks**; "
        "optimistic mid-based fills overstate opportunity. A tiny live trial ($10) is only worth considering "
        "if you monitor live books and confirm ask_up+ask_down+fees < 1 **before** sending, with hard caps."
    )
    lines.append(
        "- **Explicit: paper profits ≠ future live profits.** Latency, partial fills, stale books, and fee rounding "
        "can erase thin arb edges. Prefer remaining on paper until live book telemetry confirms fillable edge."
    )
    lines.append("")
    lines.append("## Data gaps / assumptions")
    lines.append("- No L2 order-book history for closed markets → arb uses mid/+tick approximations.")
    lines.append("- Entry uses nearest 1-minute fidelity mid after open, not VWAP or actual fills.")
    lines.append("- Assumed full fill at signal mid / approximated ask up to $10; ignored queue position & slippage.")
    lines.append("- Maker rebates / taker rebate tiers ignored (assume worst-case pure taker, no rebate).")
    lines.append("- Markets with missing price history skipped for directional signals.")
    lines.append("")
    lines.append("## Files")
    lines.append("- `report.md` (this file)")
    lines.append("- `summary.json`")
    lines.append("- `per_market.csv`")
    lines.append("- `trades.csv`")
    lines.append("")

    (OUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    n = TARGET_MARKETS
    if len(sys.argv) > 1:
        n = int(sys.argv[1])

    rows = collect_markets(n)
    if len(rows) < 10:
        print("ERROR: too few markets collected", file=sys.stderr)
        sys.exit(1)

    dir_trades = run_directional(rows)
    arb_opps = run_arb(rows)

    dir_summaries = {k: summarize_trades(k, v) for k, v in dir_trades.items()}
    arb_summaries = {k: summarize_arb(k, v, len(rows)) for k, v in arb_opps.items()}

    usable = [r for r in rows if r.entry_up_mid is not None]
    summary = {
        "generated_at_asia_shanghai": datetime.now(SH_TZ).isoformat(),
        "paper_only": True,
        "max_notional_usd": MAX_NOTIONAL,
        "fee_model": f"taker fee = shares * {FEE_RATE} * p * (1-p)",
        "sample": {
            "n_markets": len(rows),
            "n_with_entry_mids": len(usable),
            "window_ts_min": min(r.window_ts for r in rows),
            "window_ts_max": max(r.window_ts for r in rows),
            "range_asia_shanghai": [
                ts_to_sh(min(r.window_ts for r in rows)),
                ts_to_sh(max(r.window_ts for r in rows)),
            ],
            "up_wins": sum(1 for r in rows if r.winner == "Up"),
            "down_wins": sum(1 for r in rows if r.winner == "Down"),
        },
        "directional": dir_summaries,
        "arb": arb_summaries,
        "assumptions": [
            "No live orders",
            "Entry mid ~15s after window open from prices-history fidelity=1",
            "Arb asks approximated (no historical L2)",
            "Fillability: both sides up to $10 total notional",
            "Profits not guaranteed",
        ],
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    with open(OUT_DIR / "per_market.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "slug", "window_ts", "winner", "volume", "entry_up_mid", "entry_down_mid",
                "min_mid_sum", "min_ask_sum_hs", "min_ask_sum_tick", "n_price_pts", "skip_reason",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow({
                "slug": r.slug,
                "window_ts": r.window_ts,
                "winner": r.winner,
                "volume": r.volume,
                "entry_up_mid": r.entry_up_mid,
                "entry_down_mid": r.entry_down_mid,
                "min_mid_sum": r.min_mid_sum,
                "min_ask_sum_hs": r.min_ask_sum_hs,
                "min_ask_sum_tick": r.min_ask_sum_tick,
                "n_price_pts": r.n_price_pts,
                "skip_reason": r.skip_reason,
            })

    with open(OUT_DIR / "trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "slug", "strategy", "side", "entry_price", "shares", "notional", "fee",
                "winner", "won", "pnl_gross", "pnl_net",
            ],
        )
        w.writeheader()
        for trades in dir_trades.values():
            for t in trades:
                w.writerow(asdict(t))

    write_report(rows, dir_summaries, arb_summaries)
    print("Wrote:", OUT_DIR / "report.md")
    print("Wrote:", OUT_DIR / "summary.json")
    print(json.dumps(dir_summaries, indent=2))
    print("--- arb ---")
    for k, s in sorted(arb_summaries.items()):
        print(k, "opps", s["opportunities"], "net", round(s["net_pnl"], 2), "avg_edge_net", s["avg_edge_net"])


if __name__ == "__main__":
    main()
