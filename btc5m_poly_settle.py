#!/usr/bin/env python3
"""
btc5m_poly_settle.py — Polymarket *actual* settlement for BTC 5m Up/Down.

Primary source: Gamma API market by slug (btc-updown-5m-<ts>).
  - resolved when umaResolutionStatus == "resolved" (or closed + decisive
    outcomePrices near 0/1)
  - winner = outcome whose price ≈ 1.0
  - NEVER use Binance next-window-open as W/L (optional 参考 only)

CLI:
  python3 btc5m_poly_settle.py resolve btc-updown-5m-1793165700
  python3 btc5m_poly_settle.py resolve 1793165700 --side UP --fill-px 0.37 --cost 2
  python3 btc5m_poly_settle.py batch --jsonl trades.jsonl
  python3 btc5m_poly_settle.py batch --csv side,slug,ask,fill_px,cost

JSONL / CSV rows: side, slug (or window ts), decision_ask?, fill_px?, cost_usd?
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Optional

GAMMA_SLUG = "https://gamma-api.polymarket.com/markets/slug/{slug}"
DEFAULT_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""


@dataclass
class SettleResult:
    slug: str
    status: str  # RESOLVED | PENDING | ERROR
    winner: Optional[str]  # UP | DOWN | None
    source: str  # gamma | none | error
    closed: bool = False
    uma_status: Optional[str] = None
    outcomes: Optional[list] = None
    outcome_prices: Optional[list] = None
    condition_id: Optional[str] = None
    question: Optional[str] = None
    end_date: Optional[str] = None
    closed_time: Optional[str] = None
    error: Optional[str] = None
    # position evaluation (optional)
    side: Optional[str] = None
    decision_ask: Optional[float] = None
    fill_px: Optional[float] = None
    cost_usd: Optional[float] = None
    result: Optional[str] = None  # W | L | PENDING | ERROR
    pnl_usd: Optional[float] = None
    px_used: Optional[float] = None  # fill_px if known else decision_ask
    px_source: Optional[str] = None  # fill | ask | none


def _as_list(v: Any) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return []
    return list(v) if isinstance(v, (list, tuple)) else []


def _norm_side(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    u = str(s).strip().upper()
    if u in ("UP", "YES", "U"):
        return "UP"
    if u in ("DOWN", "NO", "D"):
        return "DOWN"
    return u


def normalize_slug(slug_or_ts: str) -> str:
    s = str(slug_or_ts).strip()
    if s.startswith("btc-updown-5m-"):
        return s
    if s.isdigit():
        return f"btc-updown-5m-{s}"
    return s


def _http_get_json(url: str, timeout: float = 20.0) -> dict:
    """GET JSON; honor HTTPS_PROXY / HTTP_PROXY when set."""
    # Ensure proxy env defaults match DAXUE Mac setup when unset
    if not os.environ.get("HTTPS_PROXY") and not os.environ.get("HTTP_PROXY"):
        # Prefer direct first; caller may set proxy. No forced default here so
        # CI/box without local proxy still works (gamma is public).
        pass
    handlers = []
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or DEFAULT_PROXY
    if proxy:
        handlers.append(
            urllib.request.ProxyHandler(
                {"http": proxy, "https": proxy}
            )
        )
    opener = urllib.request.build_opener(*handlers) if handlers else urllib.request.build_opener()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "btc5m-poly-settle/1.0", "Accept": "application/json"},
    )
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _winner_from_prices(outcomes: list, prices: list) -> Optional[str]:
    if not outcomes or not prices or len(outcomes) != len(prices):
        return None
    best_i = None
    best_p = -1.0
    for i, p in enumerate(prices):
        try:
            pf = float(p)
        except Exception:
            continue
        if pf > best_p:
            best_p = pf
            best_i = i
    if best_i is None:
        return None
    # Decisive settlement: winner near 1.0, loser near 0.0
    if best_p < 0.95:
        return None
    label = str(outcomes[best_i]).strip().lower()
    if label == "up" or label.startswith("up") or "yes" in label:
        return "UP"
    if label == "down" or label.startswith("down") or "no" in label:
        return "DOWN"
    # fall back by index convention [Up, Down]
    if best_i == 0:
        return "UP"
    if best_i == 1:
        return "DOWN"
    return None


def fetch_gamma_market(slug: str) -> dict:
    slug = normalize_slug(slug)
    return _http_get_json(GAMMA_SLUG.format(slug=slug))


def resolve_slug(slug: str) -> SettleResult:
    slug = normalize_slug(slug)
    try:
        m = fetch_gamma_market(slug)
    except urllib.error.HTTPError as e:
        return SettleResult(
            slug=slug,
            status="ERROR",
            winner=None,
            source="error",
            error=f"HTTP {e.code}",
        )
    except Exception as e:
        return SettleResult(
            slug=slug,
            status="ERROR",
            winner=None,
            source="error",
            error=str(e),
        )

    if not isinstance(m, dict) or not m.get("slug"):
        return SettleResult(
            slug=slug,
            status="ERROR",
            winner=None,
            source="error",
            error="empty_or_missing_market",
        )

    outcomes = _as_list(m.get("outcomes"))
    prices = _as_list(m.get("outcomePrices"))
    uma = m.get("umaResolutionStatus")
    closed = bool(m.get("closed") is True)
    winner = _winner_from_prices(outcomes, prices)

    resolved = (
        str(uma or "").lower() == "resolved"
        or (closed and winner is not None)
        or bool(m.get("automaticallyResolved") is True and winner is not None)
    )

    if resolved and winner:
        status = "RESOLVED"
    elif closed and winner is None:
        # closed but prices not decisive yet — treat as pending
        status = "PENDING"
        winner = None
    else:
        status = "PENDING"
        winner = None

    return SettleResult(
        slug=slug,
        status=status,
        winner=winner,
        source="gamma",
        closed=closed,
        uma_status=str(uma) if uma is not None else None,
        outcomes=outcomes,
        outcome_prices=[str(p) for p in prices],
        condition_id=m.get("conditionId"),
        question=m.get("question"),
        end_date=m.get("endDate") or m.get("end_date"),
        closed_time=str(m.get("closedTime") or "") or None,
    )


def pnl_for_binary_buy(
    side: str,
    winner: Optional[str],
    status: str,
    cost_usd: float,
    fill_px: Optional[float] = None,
    decision_ask: Optional[float] = None,
) -> tuple[Optional[str], Optional[float], Optional[float], Optional[str]]:
    """
    Returns (result W|L|PENDING|ERROR, pnl, px_used, px_source).
    Win pays $1 per share; shares = cost / px → pnl = cost*(1/px - 1) on win, -cost on loss.
    Prefer fill_px over decision_ask.
    """
    side_n = _norm_side(side)
    if status == "ERROR":
        return "ERROR", None, None, None
    if status != "RESOLVED" or not winner or not side_n:
        return "PENDING", None, None, None

    px = None
    px_src = None
    if fill_px is not None and float(fill_px) > 0:
        px = float(fill_px)
        px_src = "fill"
    elif decision_ask is not None and float(decision_ask) > 0:
        px = float(decision_ask)
        px_src = "ask"
    else:
        # still know W/L without px; pnl unknown
        won = side_n == winner
        return ("W" if won else "L"), None, None, "none"

    cost = float(cost_usd)
    if cost <= 0 or px <= 0 or px >= 1.0:
        won = side_n == winner
        return ("W" if won else "L"), None, px, px_src

    won = side_n == winner
    if won:
        pnl = cost * (1.0 / px - 1.0)
        return "W", round(pnl, 4), px, px_src
    return "L", round(-cost, 4), px, px_src


def evaluate_position(
    slug: str,
    side: str,
    cost_usd: float = 2.0,
    fill_px: Optional[float] = None,
    decision_ask: Optional[float] = None,
) -> SettleResult:
    r = resolve_slug(slug)
    r.side = _norm_side(side)
    r.decision_ask = float(decision_ask) if decision_ask is not None else None
    r.fill_px = float(fill_px) if fill_px is not None else None
    r.cost_usd = float(cost_usd)
    result, pnl, px_used, px_src = pnl_for_binary_buy(
        side=r.side or side,
        winner=r.winner,
        status=r.status,
        cost_usd=r.cost_usd,
        fill_px=r.fill_px,
        decision_ask=r.decision_ask,
    )
    r.result = result
    r.pnl_usd = pnl
    r.px_used = px_used
    r.px_source = px_src
    return r


def result_to_dict(r: SettleResult) -> dict:
    return asdict(r)


def _print_human(r: SettleResult) -> None:
    print(f"slug={r.slug}")
    print(f"status={r.status} winner={r.winner} source={r.source}")
    if r.uma_status:
        print(f"uma={r.uma_status} closed={r.closed}")
    if r.outcomes is not None:
        print(f"outcomes={r.outcomes} prices={r.outcome_prices}")
    if r.side:
        print(
            f"side={r.side} result={r.result} pnl={r.pnl_usd} "
            f"px_used={r.px_used} px_source={r.px_source} "
            f"ask={r.decision_ask} fill={r.fill_px} cost={r.cost_usd}"
        )
    if r.error:
        print(f"error={r.error}")


def _parse_batch_row(row: dict) -> SettleResult:
    slug = row.get("slug") or row.get("market") or row.get("window") or row.get("ts")
    side = row.get("side") or row.get("direction") or row.get("outcome")
    if not slug or not side:
        return SettleResult(
            slug=str(slug or ""),
            status="ERROR",
            winner=None,
            source="error",
            error="missing slug or side",
            side=_norm_side(side),
        )
    cost = row.get("cost_usd", row.get("cost", row.get("amount", 2)))
    ask = row.get("decision_ask", row.get("ask"))
    fill = row.get("fill_px", row.get("fill"))
    return evaluate_position(
        slug=str(slug),
        side=str(side),
        cost_usd=float(cost) if cost not in (None, "") else 2.0,
        fill_px=float(fill) if fill not in (None, "") else None,
        decision_ask=float(ask) if ask not in (None, "") else None,
    )


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description="Polymarket BTC 5m actual settlement (gamma)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("resolve", help="Resolve one slug / evaluate one position")
    pr.add_argument("slug")
    pr.add_argument("--side", default=None)
    pr.add_argument("--fill-px", type=float, default=None)
    pr.add_argument("--ask", "--decision-ask", dest="ask", type=float, default=None)
    pr.add_argument("--cost", type=float, default=2.0)
    pr.add_argument("--json", action="store_true")

    pb = sub.add_parser("batch", help="Batch evaluate JSONL or CSV rows")
    pb.add_argument("--jsonl", default=None, help="Path to JSONL (one object per line)")
    pb.add_argument("--csv", default=None, help="Path to CSV, or '-' for stdin")
    pb.add_argument("--json", action="store_true", help="Emit JSON array")
    pb.add_argument("--md", action="store_true", help="Emit markdown table")

    args = p.parse_args(argv)

    if args.cmd == "resolve":
        if args.side:
            r = evaluate_position(
                args.slug,
                args.side,
                cost_usd=args.cost,
                fill_px=args.fill_px,
                decision_ask=args.ask,
            )
        else:
            r = resolve_slug(args.slug)
        if args.json:
            print(json.dumps(result_to_dict(r), ensure_ascii=False))
        else:
            _print_human(r)
        return 0 if r.status != "ERROR" else 2

    # batch
    rows: list[dict] = []
    if args.jsonl:
        with open(args.jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    elif args.csv:
        src = sys.stdin if args.csv == "-" else open(args.csv, encoding="utf-8")
        try:
            reader = csv.DictReader(src)
            rows = list(reader)
        finally:
            if args.csv != "-":
                src.close()
    else:
        p.error("batch requires --jsonl or --csv")

    results = [_parse_batch_row(row) for row in rows]
    if args.json:
        print(json.dumps([result_to_dict(r) for r in results], ensure_ascii=False, indent=2))
    elif args.md:
        print(
            "| # | side | slug | ask | fill | winner | W/L | pnl | source | status |"
        )
        print("|---|------|------|-----|------|--------|-----|-----|--------|--------|")
        for i, r in enumerate(results, 1):
            slug_short = r.slug.replace("btc-updown-5m-", "")
            print(
                f"| {i} | {r.side or '-'} | {slug_short} | "
                f"{r.decision_ask if r.decision_ask is not None else '-'} | "
                f"{r.fill_px if r.fill_px is not None else '-'} | "
                f"{r.winner or '-'} | {r.result or '-'} | "
                f"{r.pnl_usd if r.pnl_usd is not None else '-'} | "
                f"{r.source} | {r.status} |"
            )
        w = sum(1 for r in results if r.result == "W")
        l = sum(1 for r in results if r.result == "L")
        pend = sum(1 for r in results if r.result == "PENDING")
        pnl = sum(r.pnl_usd or 0 for r in results if r.pnl_usd is not None)
        print(f"\nSummary: W={w} L={l} PENDING={pend} pnl≈{pnl:+.2f} (source=gamma)")
    else:
        for r in results:
            _print_human(r)
            print("---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
