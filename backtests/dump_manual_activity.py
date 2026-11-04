#!/usr/bin/env python3
"""Dump N-day Polymarket wallet activity for manual_fav_chase session replay.

Public Data API v2 only (no .env secrets printed; funder read from env if present).
Does NOT touch live bots / .env writes.

Examples (from /Users/nick/poly-test):
  python3 backtests/dump_manual_activity.py --days 7
  python3 backtests/dump_manual_activity.py --start-sh 2026-10-27 --end-sh 2026-11-03
  python3 backtests/dump_manual_activity.py --days 7 --session week_20261027_1103

Then replay:
  python3 backtests/manual_fav_chase_backtest.py --session week_20261027_1103 --rule ALL
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
OUT_DIR = ROOT / "backtests" / "manual_fav_chase"
SH_TZ = timezone(timedelta(hours=8))


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


def mask_addr(a: str) -> str:
    a = a.strip()
    return f"{a[:6]}…{a[-4:]}" if len(a) >= 12 else "***"


def load_funder(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit.strip()
    # Prefer env already exported; else parse .env keys without printing values.
    for key in (
        "POLYMARKET_FUNDER_ADDRESS",
        "POLYMARKET_WALLET_ADDRESS",
        "FUNDER_ADDRESS",
        "PROXY_WALLET",
    ):
        v = os.environ.get(key)
        if v and v.startswith("0x") and len(v) >= 42:
            return v.strip()
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line or line.strip().startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in (
                "POLYMARKET_FUNDER_ADDRESS",
                "POLYMARKET_WALLET_ADDRESS",
                "FUNDER_ADDRESS",
                "PROXY_WALLET",
            ) and v.startswith("0x") and len(v) >= 42:
                return v
    raise SystemExit(
        "No funder address found. Pass --user 0x… or set POLYMARKET_FUNDER_ADDRESS."
    )


def http_get(url: str, timeout: float = 30.0, retries: int = 4) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "poly-test-dump-manual-activity/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(0.4 * (2 ** attempt))
    raise RuntimeError(f"GET failed {url}: {last_err}")


def sh_day_range(start_sh: str, end_sh: str) -> tuple[int, int]:
    """Inclusive Shanghai calendar dates → [start_epoch, end_epoch)."""
    d0 = datetime.strptime(start_sh, "%Y-%m-%d").replace(tzinfo=SH_TZ)
    d1 = datetime.strptime(end_sh, "%Y-%m-%d").replace(tzinfo=SH_TZ) + timedelta(days=1)
    return int(d0.timestamp()), int(d1.timestamp())


def fetch_activity(user: str, start: int, end: int, limit: int = 100) -> List[dict]:
    """Cursor-paginate Data API v2 activity in [start, end)."""
    out: List[dict] = []
    cursor: Optional[str] = None
    pages = 0
    while True:
        q: Dict[str, Any] = {"user": user, "limit": limit, "start": start, "end": end}
        if cursor:
            q["cursor"] = cursor
        url = "https://data-api.polymarket.com/v2/activity?" + urllib.parse.urlencode(q)
        data = http_get(url)
        pages += 1
        items = data.get("data") if isinstance(data, dict) else data
        items = items or []
        out.extend(items)
        pag = (data.get("pagination") if isinstance(data, dict) else None) or {}
        print(
            f"[dump] page={pages} got={len(items)} total={len(out)} has_more={pag.get('has_more')}",
            flush=True,
        )
        if not pag.get("has_more") or not pag.get("next_cursor") or not items:
            break
        cursor = pag["next_cursor"]
        time.sleep(0.12)
        if pages >= 200:
            print("[dump] hit page cap 200; stopping", flush=True)
            break
    # Keep only in-range; newest-first API → sort ascending for FIFO replay
    filtered = [a for a in out if start <= int(a.get("timestamp") or 0) < end]
    filtered.sort(key=lambda a: (int(a.get("timestamp") or 0), a.get("transaction_hash") or ""))
    return filtered


def default_session_id(start_sh: str, end_sh: str) -> str:
    a = start_sh.replace("-", "")
    b = end_sh.replace("-", "")
    return f"{a}_{b}"


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Dump N-day manual_activity JSON for fav-chase replay")
    p.add_argument("--days", type=int, default=0, help="Last N Shanghai days ending today (inclusive)")
    p.add_argument("--start-sh", type=str, default=None, help="YYYY-MM-DD Asia/Shanghai inclusive")
    p.add_argument("--end-sh", type=str, default=None, help="YYYY-MM-DD Asia/Shanghai inclusive")
    p.add_argument("--session", type=str, default=None, help="Session id → logs/manual_activity_{id}.json")
    p.add_argument("--user", type=str, default=None, help="Funder/proxy 0x… (optional; else .env)")
    p.add_argument("--limit", type=int, default=100, help="Page size (max ~100)")
    args = p.parse_args(argv)

    now_sh = datetime.now(SH_TZ)
    if args.start_sh and args.end_sh:
        start_sh, end_sh = args.start_sh, args.end_sh
    elif args.days and args.days > 0:
        end_sh = now_sh.strftime("%Y-%m-%d")
        start_sh = (now_sh - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    else:
        print("Specify --days N or --start-sh/--end-sh", file=sys.stderr)
        return 2

    start_e, end_e = sh_day_range(start_sh, end_sh)
    # If end_sh is today, cap end at now (not tomorrow 00:00) so dump is "through now"
    if end_sh == now_sh.strftime("%Y-%m-%d"):
        end_e = min(end_e, int(now_sh.timestamp()) + 1)

    user = load_funder(args.user)
    session_id = args.session or default_session_id(start_sh, end_sh)
    print(
        f"[dump] funder={mask_addr(user)} session={session_id}\n"
        f"[dump] range_sh={start_sh}→{end_sh} epochs=[{start_e},{end_e})",
        flush=True,
    )

    activity = fetch_activity(user, start_e, end_e, limit=max(1, min(args.limit, 100)))
    trades = [a for a in activity if str(a.get("type") or "").upper() == "TRADE"]
    payload = {
        "activity": activity,
        "trades": trades,
        "wide": [],
        "meta": {
            "win_start_epoch": start_e,
            "win_end_epoch": end_e,
            "ctx_start": start_e,
            "ctx_end": end_e - 1,
            "source": "data-api.polymarket.com/v2/activity",
            "funder_mask": mask_addr(user),
            "start_sh": start_sh,
            "end_sh": end_sh,
            "dumped_at_asia_shanghai": now_sh.isoformat(),
            "n_activity": len(activity),
            "n_trades": len(trades),
        },
    }
    LOGS.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_logs = LOGS / f"manual_activity_{session_id}.json"
    out_copy = OUT_DIR / f"manual_activity_{session_id}.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    out_logs.write_text(text, encoding="utf-8")
    out_copy.write_text(text, encoding="utf-8")
    print(f"[dump] wrote {out_logs}")
    print(f"[dump] wrote {out_copy}")
    print(f"[dump] activity={len(activity)} trades={len(trades)}")
    if trades:
        t0 = int(trades[0]["timestamp"])
        t1 = int(trades[-1]["timestamp"])
        print(
            f"[dump] trade_span={datetime.fromtimestamp(t0, SH_TZ)} → {datetime.fromtimestamp(t1, SH_TZ)}"
        )
    print(
        f"\nReplay:\n  python3 backtests/manual_fav_chase_backtest.py --session {session_id} --rule ALL\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
