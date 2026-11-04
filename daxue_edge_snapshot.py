#!/usr/bin/env python3
"""
daxue_edge_snapshot.py — Edge verification helper for DAXUE goal layering.

Loads today's (and optional last N calendar days Asia/Shanghai) FILL logs,
settles via Gamma (btc5m_poly_settle), prints:
  n, WR, mean_fill, WR-mean_fill, day_pnl, vs DAY_LOSS_STOP / BIG_WIN_LOCK / DAY_PROFIT_LOCK

Writes logs/reviews/daxue_edge_YYYYMMDD.md

Examples:
  python3 daxue_edge_snapshot.py
  python3 daxue_edge_snapshot.py --days 3
  python3 daxue_edge_snapshot.py --date 20261028 --no-write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from daxue_five_trade_review import dedupe_fills, iter_fill_lines, settle_fills
from daxue_day_risk import day_state_path

SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
REVIEWS_DIR = LOGS_DIR / "reviews"
TZ = ZoneInfo("Asia/Shanghai")


def _fenv(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, os.getenv(f"DAXUE_{name}", str(default))))
    except ValueError:
        return float(default)


def _truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_dotenv() -> None:
    p = SCRIPT_DIR / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k not in os.environ:
            os.environ[k] = v


def day_bounds(day: str) -> tuple[datetime, datetime]:
    """day=YYYYMMDD → [start, end) Asia/Shanghai."""
    dt = datetime.strptime(day, "%Y%m%d").replace(tzinfo=TZ)
    return dt, dt + timedelta(days=1)


def fills_for_day(day: str) -> list[dict]:
    start, end = day_bounds(day)
    fills = dedupe_fills(list(iter_fill_lines()))
    fills = [f for f in fills if start <= f["ts"] < end]
    fills.sort(key=lambda x: x["ts"])
    return fills


def summarize(rows: list[dict], day: str) -> dict:
    settled = [r for r in rows if r.get("result") in ("W", "L")]
    pending = [r for r in rows if r.get("result") == "PENDING"]
    w = sum(1 for r in settled if r["result"] == "W")
    l = sum(1 for r in settled if r["result"] == "L")
    n = len(rows)
    n_decided = w + l
    wr = (w / n_decided) if n_decided else None
    fills_px = [
        float(r["fill_px"])
        for r in rows
        if r.get("fill_px") is not None and float(r["fill_px"]) > 0
    ]
    if not fills_px:
        fills_px = [
            float(r["decision_ask"])
            for r in rows
            if r.get("decision_ask") is not None and float(r["decision_ask"]) > 0
        ]
    mean_fill = (sum(fills_px) / len(fills_px)) if fills_px else None
    edge = (wr - mean_fill) if (wr is not None and mean_fill is not None) else None
    pnls = [r["pnl"] for r in settled if r.get("pnl") is not None]
    day_pnl = sum(pnls) if pnls else 0.0
    day_loss_stop = _fenv("DAY_LOSS_STOP", 7.0)
    big_win = _fenv("BIG_WIN_LOCK", _fenv("LOCK_WIN_PNL", 6.0))
    day_profit_lock = _fenv("DAY_PROFIT_LOCK", _fenv("DAXUE_DAY_PROFIT_LOCK", 15.0))
    lock = _truthy(os.getenv("LOCK_GAINS", os.getenv("DAXUE_LOCK_GAINS", "1")))
    max_leg = max((float(r["pnl"]) for r in settled if r.get("pnl") is not None), default=0.0)
    return {
        "day": day,
        "n": n,
        "n_w": w,
        "n_l": l,
        "n_pending": len(pending),
        "wr": wr,
        "mean_fill": mean_fill,
        "edge_wr_minus_mean_fill": edge,
        "day_pnl": round(day_pnl, 4),
        "DAY_LOSS_STOP": day_loss_stop,
        "BIG_WIN_LOCK": big_win,
        "DAY_PROFIT_LOCK": day_profit_lock,
        "LOCK_GAINS": lock,
        "max_single_leg_pnl": round(max_leg, 4),
        "vs_day_loss_stop": round(day_pnl + day_loss_stop, 4),  # >0 = cushion
        "hit_big_win_lock": bool(max_leg > big_win),
        "hit_day_loss_stop": bool(day_pnl <= -day_loss_stop),
        "hit_day_profit_lock": bool(day_pnl >= day_profit_lock),
        "rows": rows,
    }


def markdown(summary: dict, multi: Optional[list[dict]] = None) -> str:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M Asia/Shanghai")
    s = summary
    lines = [
        f"# DAXUE edge snapshot {s['day']} ({now})",
        "",
        "结算：**Polymarket Gamma**（`btc5m_poly_settle`）。目标分层：保本金 → 验边 → 日收益。",
        "",
        "## Today",
        "",
        f"- n={s['n']} (W={s['n_w']} L={s['n_l']} PENDING={s['n_pending']})",
        f"- WR={('%.1f%%' % (100*s['wr'])) if s['wr'] is not None else 'n/a'}",
        f"- mean_fill={s['mean_fill']:.4f}" if s["mean_fill"] is not None else "- mean_fill=n/a",
        (
            f"- WR−mean_fill={s['edge_wr_minus_mean_fill']:+.4f}"
            if s["edge_wr_minus_mean_fill"] is not None
            else "- WR−mean_fill=n/a"
        ),
        f"- day_pnl=${s['day_pnl']:+.2f}",
        f"- vs DAY_LOSS_STOP=${s['DAY_LOSS_STOP']:.2f}: cushion=${s['vs_day_loss_stop']:+.2f} "
        f"(hit_stop={s['hit_day_loss_stop']})",
        f"- vs BIG_WIN_LOCK=${s['BIG_WIN_LOCK']:.2f}: max_leg=${s['max_single_leg_pnl']:+.2f} "
        f"hit={s['hit_big_win_lock']} (LOCK_GAINS={int(s['LOCK_GAINS'])})",
        f"- vs DAY_PROFIT_LOCK=${s['DAY_PROFIT_LOCK']:.2f}: day_pnl=${s['day_pnl']:+.2f} "
        f"hit={s['hit_day_profit_lock']}",
        "",
    ]
    # day state file if present
    st = day_state_path(LOGS_DIR, s["day"])
    if st.is_file():
        try:
            raw = json.loads(st.read_text(encoding="utf-8"))
            lines.append(
                f"- live day_state: settled=${float(raw.get('day_pnl_settled') or 0):+.2f} "
                f"streak_L={raw.get('consec_losses')} "
                f"day_loss_stopped={raw.get('day_loss_stopped')} "
                f"lock_gains_stopped={raw.get('lock_gains_stopped')} "
                f"day_profit_stopped={raw.get('day_profit_stopped')}"
            )
            lines.append("")
        except Exception:
            pass

    lines += [
        "| # | time | side | ask | fill | W/L | pnl | slug |",
        "|---|------|------|-----|------|-----|-----|------|",
    ]
    for i, r in enumerate(s["rows"], 1):
        ask = f"{r['decision_ask']:.3f}" if r.get("decision_ask") is not None else "-"
        fill = f"{r['fill_px']:.4g}" if r.get("fill_px") is not None else "-"
        pnl = f"{r['pnl']:+.2f}" if r.get("pnl") is not None else "-"
        lines.append(
            f"| {i} | {r.get('time','-')} | {r.get('side','-')} | {ask} | {fill} | "
            f"{r.get('result') or '-'} | {pnl} | {r.get('slug_ts') or '-'} |"
        )

    if multi and len(multi) > 1:
        lines += ["", "## Last N days", "", "| day | n | WR | mean_fill | edge | day_pnl |", "|-----|---|----|-----------|------|---------|"]
        for m in multi:
            wr = f"{100*m['wr']:.0f}%" if m["wr"] is not None else "-"
            mf = f"{m['mean_fill']:.3f}" if m["mean_fill"] is not None else "-"
            ed = (
                f"{m['edge_wr_minus_mean_fill']:+.3f}"
                if m["edge_wr_minus_mean_fill"] is not None
                else "-"
            )
            lines.append(
                f"| {m['day']} | {m['n']} | {wr} | {mf} | {ed} | ${m['day_pnl']:+.2f} |"
            )

    lines.append("")
    return "\n".join(lines)


def print_console(s: dict) -> None:
    wr = f"{100*s['wr']:.1f}%" if s["wr"] is not None else "n/a"
    mf = f"{s['mean_fill']:.4f}" if s["mean_fill"] is not None else "n/a"
    ed = (
        f"{s['edge_wr_minus_mean_fill']:+.4f}"
        if s["edge_wr_minus_mean_fill"] is not None
        else "n/a"
    )
    print(
        f"day={s['day']} n={s['n']} W={s['n_w']} L={s['n_l']} PENDING={s['n_pending']} "
        f"WR={wr} mean_fill={mf} WR-mean_fill={ed} day_pnl=${s['day_pnl']:+.2f} "
        f"vs_DAY_LOSS_STOP(cushion)=${s['vs_day_loss_stop']:+.2f} "
        f"max_leg=${s['max_single_leg_pnl']:+.2f} BIG_WIN_LOCK=${s['BIG_WIN_LOCK']:.2f} "
        f"big_win_hit={s['hit_big_win_lock']} day_loss_hit={s['hit_day_loss_stop']} "
        f"DAY_PROFIT_LOCK=${s['DAY_PROFIT_LOCK']:.2f} cum_hit={s['hit_day_profit_lock']}"
    )


def main(argv: Optional[list] = None) -> int:
    load_dotenv()
    # Prefer Clash proxy like live
    os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
    os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")

    ap = argparse.ArgumentParser(description="DAXUE edge snapshot (Gamma settle)")
    ap.add_argument("--date", default=None, help="YYYYMMDD Asia/Shanghai (default today)")
    ap.add_argument("--days", type=int, default=1, help="Include last N calendar days")
    ap.add_argument("--cost", type=float, default=2.0)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    today = args.date or datetime.now(TZ).strftime("%Y%m%d")
    n_days = max(1, int(args.days))
    days = []
    base = datetime.strptime(today, "%Y%m%d")
    for i in range(n_days - 1, -1, -1):
        days.append((base - timedelta(days=i)).strftime("%Y%m%d"))

    summaries = []
    for d in days:
        fills = fills_for_day(d)
        rows = settle_fills(fills, cost=args.cost) if fills else []
        summaries.append(summarize(rows, d))

    focus = summaries[-1]
    if args.json:
        out = {**{k: v for k, v in focus.items() if k != "rows"}, "history": [
            {k: v for k, v in s.items() if k != "rows"} for s in summaries
        ]}
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_console(focus)
        for s in summaries[:-1]:
            print_console(s)
        md = markdown(focus, multi=summaries if n_days > 1 else None)
        if not args.no_write:
            REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
            out_path = REVIEWS_DIR / f"daxue_edge_{focus['day']}.md"
            out_path.write_text(md, encoding="utf-8")
            print(f"Wrote {out_path}", file=sys.stderr)
        else:
            print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
