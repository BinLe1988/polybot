#!/usr/bin/env python3
"""
daxue_five_trade_review.py — Build DAXUE 五笔复盘 from FILL logs using
Polymarket *actual* gamma settlement (btc5m_poly_settle.py).

Does NOT use next-window-open as W/L. Optional --note-next-open only adds a
参考 column; never drives result/pnl.

Examples:
  python3 daxue_five_trade_review.py --last 5 --md
  python3 daxue_five_trade_review.py --after '2026-10-28 16:38' --last 5 --write
  python3 daxue_five_trade_review.py --slugs 1793165700,1793174700 --sides UP,DOWN --asks 0.37,0.22 --fills 0.37,0.19
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from btc5m_poly_settle import evaluate_position, normalize_slug, resolve_slug

SCRIPT_DIR = Path(__file__).resolve().parent
LOGS_DIR = SCRIPT_DIR / "logs"
REVIEWS_DIR = LOGS_DIR / "reviews"
TZ = ZoneInfo("Asia/Shanghai")

FILL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})"
    r".*?\[DAXUE\]\[FILL\]\s+"
    r"(?P<side>UP|DOWN)\s+"
    r"(?:decision_ask=(?P<dask>[0-9.]+)\s+)?"
    r"(?:ask=(?P<ask>[0-9.]+)\s+)?"
    r"(?:fill_px=(?P<fill>[0-9.]+)\s+)?"
    r".*?slug=(?P<slug>btc-updown-5m-\d+|\d+)",
)


def _parse_ts(s: str, default_date: Optional[str] = None) -> datetime:
    s = s.strip()
    if re.match(r"^\d{2}:\d{2}:\d{2}$", s):
        day = default_date or datetime.now(TZ).strftime("%Y-%m-%d")
        s = f"{day} {s}"
    # log lines are local Asia/Shanghai wall clock
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=TZ)


def iter_fill_lines(log_glob: str = "daxue_live_*.log"):
    files = sorted(LOGS_DIR.glob(log_glob))
    for path in files:
        # Infer date from filename daxue_live_YYYYMMDD_HHMMSS.log
        m = re.search(r"daxue_live_(\d{8})_", path.name)
        day = None
        if m:
            raw = m.group(1)
            day = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            if "[DAXUE][FILL]" not in line:
                continue
            # strip leading logger prefix if present: "2026-10-28 13:38:01 | INFO | ..."
            mm = FILL_RE.search(line)
            if not mm:
                # try with full datetime already in line via alternative parse
                continue
            g = mm.groupdict()
            ts_raw = g["ts"]
            try:
                ts = _parse_ts(ts_raw, default_date=day)
            except Exception:
                continue
            ask = g.get("dask") or g.get("ask")
            fill = g.get("fill")
            yield {
                "ts": ts,
                "side": g["side"],
                "decision_ask": float(ask) if ask else None,
                "fill_px": float(fill) if fill else None,
                "slug": normalize_slug(g["slug"]),
                "log": path.name,
                "raw": line,
            }


def dedupe_fills(fills: list[dict]) -> list[dict]:
    """Logs are often duplicated across rotated files; keep first per order-ish key."""
    seen = set()
    out = []
    for f in fills:
        # key: slug+side+minute (fills rarely double in same minute same slug)
        key = (f["slug"], f["side"], f["ts"].strftime("%Y-%m-%d %H:%M"))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def select_fills(
    after: Optional[datetime] = None,
    before: Optional[datetime] = None,
    last: Optional[int] = None,
) -> list[dict]:
    fills = dedupe_fills(list(iter_fill_lines()))
    fills.sort(key=lambda x: x["ts"])
    if after:
        fills = [f for f in fills if f["ts"] > after]
    if before:
        fills = [f for f in fills if f["ts"] < before]
    if last is not None:
        fills = fills[-last:]
    return fills


def settle_fills(fills: list[dict], cost: float = 2.0) -> list[dict]:
    rows = []
    for f in fills:
        r = evaluate_position(
            slug=f["slug"],
            side=f["side"],
            cost_usd=cost,
            fill_px=f.get("fill_px"),
            decision_ask=f.get("decision_ask"),
        )
        rows.append(
            {
                "time": f["ts"].strftime("%H:%M"),
                "ts": f["ts"].isoformat(),
                "side": f["side"],
                "decision_ask": f.get("decision_ask"),
                "fill_px": f.get("fill_px"),
                "cost": cost,
                "slug": f["slug"],
                "slug_ts": f["slug"].replace("btc-updown-5m-", ""),
                "actual_winner": r.winner,
                "result": r.result,
                "pnl": r.pnl_usd,
                "source": r.source,
                "status": r.status,
                "px_used": r.px_used,
                "px_source": r.px_source,
                "condition_id": r.condition_id,
            }
        )
    return rows


def markdown_table(rows: list[dict], title: Optional[str] = None) -> str:
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M Asia/Shanghai")
    lines = []
    if title:
        lines.append(f"# {title}")
    else:
        lines.append(f"# DAXUE 五笔复盘 {now}")
    lines.append("")
    lines.append(
        "结算判定：**Polymarket Gamma 实际结算**（`umaResolutionStatus=resolved` / "
        "`outcomePrices` 胜出 outcome）。未决市场标 **PENDING**，不用 next-open 猜 W/L。"
        "盈亏优先 fill_px，否则 decision_ask：胜=$cost×(1/px−1)，负=−$cost。"
    )
    lines.append("")
    lines.append(
        "| # | 时间 | 方向 | ask | fill_px | 成本 | slug | 实际赢家 | W/L | PnL | source |"
    )
    lines.append(
        "|---|------|------|-----|---------|------|------|----------|-----|-----|--------|"
    )
    for i, r in enumerate(rows, 1):
        ask = f"{r['decision_ask']:.3f}" if r["decision_ask"] is not None else "-"
        fill = f"{r['fill_px']:.4g}" if r["fill_px"] is not None else "-"
        pnl = f"{r['pnl']:+.2f}" if r["pnl"] is not None else "-"
        wl = r["result"] or "-"
        winner = r["actual_winner"] or ("?" if r["status"] == "PENDING" else "-")
        lines.append(
            f"| {i} | {r['time']} | {r['side']} | {ask} | {fill} | ${r['cost']:.0f} | "
            f"{r['slug_ts']} | {winner} | {wl} | {pnl} | {r['source']} |"
        )
    w = sum(1 for r in rows if r["result"] == "W")
    l = sum(1 for r in rows if r["result"] == "L")
    pend = sum(1 for r in rows if r["result"] == "PENDING")
    pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
    net = sum(pnls) if pnls else None
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(
        f"- 成交 {len(rows)} / 胜 {w} / 负 {l}"
        + (f" / PENDING {pend}" if pend else "")
        + (f"；胜率 {100*w/max(1,w+l):.0f}%" if (w + l) else "")
    )
    if net is not None:
        lines.append(f"- 净盈亏约 **{net:+.2f}**（按 fill_px 优先；source=gamma）")
    lines.append(
        "- 参考：next-open vs window-open **仅作对照备注，不作为 W/L**（本表未写入该列）。"
    )
    return "\n".join(lines) + "\n"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="DAXUE five-trade review via gamma settle")
    ap.add_argument("--last", type=int, default=5, help="Last N fills (default 5)")
    ap.add_argument("--after", default=None, help="Only fills after 'YYYY-MM-DD HH:MM[:SS]' Asia/Shanghai")
    ap.add_argument("--before", default=None, help="Only fills before timestamp")
    ap.add_argument("--cost", type=float, default=2.0)
    ap.add_argument("--md", action="store_true", help="Print markdown (default)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--write",
        action="store_true",
        help="Write logs/reviews/daxue_review_YYYYMMDD_HHMM.md",
    )
    ap.add_argument(
        "--manual",
        default=None,
        help="Manual CSV: time,side,slug,ask,fill_px[,cost] (no log parse)",
    )
    args = ap.parse_args(argv)

    if args.manual:
        rows_in = []
        for line in Path(args.manual).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("time"):
                continue
            parts = [p.strip() for p in line.split(",")]
            # time,side,slug,ask,fill,cost?
            t, side, slug = parts[0], parts[1], parts[2]
            ask = float(parts[3]) if len(parts) > 3 and parts[3] else None
            fill = float(parts[4]) if len(parts) > 4 and parts[4] else None
            cost = float(parts[5]) if len(parts) > 5 and parts[5] else args.cost
            try:
                ts = _parse_ts(t if " " in t else f"{datetime.now(TZ).date()} {t}")
            except Exception:
                ts = datetime.now(TZ)
            rows_in.append(
                {
                    "ts": ts,
                    "side": side.upper(),
                    "decision_ask": ask,
                    "fill_px": fill,
                    "slug": normalize_slug(slug),
                }
            )
        settled = settle_fills(rows_in, cost=args.cost)
    else:
        after = _parse_ts(args.after) if args.after else None
        before = _parse_ts(args.before) if args.before else None
        fills = select_fills(after=after, before=before, last=args.last)
        if not fills:
            print("No FILL rows matched.", file=sys.stderr)
            return 1
        settled = settle_fills(fills, cost=args.cost)

    if args.json:
        print(json.dumps(settled, ensure_ascii=False, indent=2))
    else:
        md = markdown_table(settled)
        print(md)
        if args.write:
            REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M")
            path = REVIEWS_DIR / f"daxue_review_{stamp}.md"
            path.write_text(md, encoding="utf-8")
            print(f"Wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
