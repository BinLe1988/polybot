#!/usr/bin/env python3
"""
daxue_day_risk.py — Day capital-protection state for DAXUE live.

Asia/Shanghai calendar day. Persists logs/daxue_day_state_YYYYMMDD.json.

Env knobs (bare names preferred; DAXUE_* aliases accepted by live CONFIG):
  DAY_LOSS_STOP (default 7) — stop new ENTRYs when day_pnl_for_stop ≤ −threshold
  CONSEC_LOSS_PAUSE (default 3)
  PAUSE_MIN (default 30)
  BIG_WIN_LOCK (default 6) — after any single settled leg PnL > threshold, lock day
  LOCK_GAINS (default 1) — master enable for BIG_WIN_LOCK (1=on)
  DAY_PROFIT_LOCK (default 15) — lock when (day_pnl_settled − profit_lock_baseline) ≥ threshold
  DAILY_TARGET_SOFT — legacy soft day-cumulative lock; unused (compat only)

v1 limits:
  - day_pnl_settled only counts Gamma-RESOLVED fills recorded here
  - provisional = settled − open live costs (worst-case: all open lose)
  - stop uses min(settled, provisional) vs −DAY_LOSS_STOP
  - consecutive losses only from settled W/L
  - lock gains = single-window big win OR (day_pnl_settled − profit_lock_baseline) ≥ DAY_PROFIT_LOCK
  - manual_unlock sets profit_lock_baseline=day_pnl_settled so another +DAY_PROFIT_LOCK
    of settled PnL is required before DAY_PROFIT_LOCK fires again
  - after manual_resume, DAY_LOSS_STOP only re-fires if (day_pnl_for_stop − baseline)
    ≤ −DAY_LOSS_STOP (further loss from unlock point), not the pre-unlock absolute PnL
  - PnL accounting (2026-11-04) is NET of estimated taker fees: rate from config
    TAKER_FEE_RATE (default 0.07), fee = shares·rate·p·(1−p) on the entry fill,
    plus the exit leg when an EARLY_EXIT sell passes exit_px. Settled records
    carry pnl (net), pnl_gross and fee_est; stops/locks consume NET figures.
  - day_*_all aggregates (day_pnl_gross_all / day_fees_all / day_pnl_net_all /
    n_w_all / n_l_all) make the full-day gross/fee/net split explicit (DAXUE
    counters are never zeroed intraday; only day rollover clears them).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from btc5m_poly_settle import evaluate_position

TZ_SH = ZoneInfo("Asia/Shanghai")
log = logging.getLogger("daxue")


def taker_fee_usd(shares: float, price: float, rate: float) -> float:
    """Polymarket taker fee: shares · rate · p · (1−p)."""
    p = max(0.0, min(1.0, float(price)))
    return float(shares) * float(rate) * p * (1.0 - p)


def day_state_path(logs_dir: Path, day: Optional[str] = None) -> Path:
    if not day:
        day = datetime.now(TZ_SH).strftime("%Y%m%d")
    return Path(logs_dir) / f"daxue_day_state_{day}.json"


class DayRiskState:
    def __init__(self, logs_dir: Path, config: dict):
        self.logs_dir = Path(logs_dir)
        self.config = config
        self.day = datetime.now(TZ_SH).strftime("%Y%m%d")
        self.day_pnl_settled = 0.0
        self.consec_losses = 0
        self.day_loss_stopped = False
        self.lock_gains_stopped = False
        self.day_profit_stopped = False
        # After manual_unlock: require another +DAY_PROFIT_LOCK of settled PnL
        # vs this baseline before DAY_PROFIT_LOCK fires again
        # (i.e. day_pnl_settled - profit_lock_baseline >= DAY_PROFIT_LOCK).
        self.profit_lock_baseline = 0.0
        # Human unlock for the rest of the Asia/Shanghai day: ignore
        # BIG_WIN_LOCK legs settled at/before manual_resume_ts; a later
        # single-leg win > BIG_WIN_LOCK locks again and clears this flag.
        # Same resume window also gates DAY_PROFIT_LOCK via profit_lock_baseline,
        # and DAY_LOSS_STOP via further-loss-vs-baseline (see check_day_loss).
        self.manual_resume = False
        self.manual_resume_ts = 0.0
        self.pause_until = 0.0
        self.settled: dict = {}
        self.pending: dict = {}
        self.n_w = 0
        self.n_l = 0
        # Full-day gross/fee/net split over ALL legs settled today.
        # Only day rollover clears them (DAXUE never zeroes counters intraday).
        self.day_pnl_gross_all = 0.0
        self.day_fees_all = 0.0
        self.day_pnl_net_all = 0.0
        self.n_w_all = 0
        self.n_l_all = 0
        self.load()

    def path(self) -> Path:
        return day_state_path(self.logs_dir, self.day)

    def load(self) -> None:
        p = self.path()
        if not p.is_file():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"[DAXUE] day_state load failed: {e}")
            return
        if str(raw.get("date") or "") != self.day:
            return
        self.day_pnl_settled = float(raw.get("day_pnl_settled") or 0)
        self.consec_losses = int(raw.get("consec_losses") or 0)
        self.day_loss_stopped = bool(raw.get("day_loss_stopped"))
        self.lock_gains_stopped = bool(raw.get("lock_gains_stopped"))
        self.day_profit_stopped = bool(raw.get("day_profit_stopped"))
        self.profit_lock_baseline = float(raw.get("profit_lock_baseline") or 0)
        self.manual_resume = bool(raw.get("manual_resume"))
        self.manual_resume_ts = float(raw.get("manual_resume_ts") or 0)
        self.pause_until = float(raw.get("pause_until") or 0)
        self.settled = dict(raw.get("settled") or {})
        self.pending = dict(raw.get("pending") or {})
        self.n_w = int(raw.get("n_w") or 0)
        self.n_l = int(raw.get("n_l") or 0)
        self.day_pnl_gross_all = float(raw.get("day_pnl_gross_all") or 0)
        self.day_fees_all = float(raw.get("day_fees_all") or 0)
        self.day_pnl_net_all = float(raw.get("day_pnl_net_all") or 0)
        self.n_w_all = int(raw.get("n_w_all") or 0)
        self.n_l_all = int(raw.get("n_l_all") or 0)
        if not raw.get("fee_model"):
            self._normalize_legacy_fees()
        # Re-apply current rules (clears legacy soft-target-only locks;
        # keeps lock if any settled leg already exceeds BIG_WIN_LOCK;
        # sticky DAY_PROFIT_LOCK if (pnl − baseline) ≥ threshold).
        self.recompute_lock_gains(persist=True)
        self.recompute_day_profit_lock(persist=True)

    def fee_rate(self) -> float:
        v = self.config.get("TAKER_FEE_RATE")
        try:
            return 0.07 if v is None else float(v)
        except (TypeError, ValueError):
            return 0.07

    def _estimate_fee(self, cost_usd: float, fill_px: float, exit_px: float = 0.0) -> float:
        cost = float(cost_usd or 0)
        fill = float(fill_px or 0)
        if cost <= 0 or fill <= 0 or fill >= 1:
            return 0.0
        shares = cost / fill
        fee = taker_fee_usd(shares, fill, self.fee_rate())
        ex = float(exit_px or 0)
        if 0 < ex < 1:
            fee += taker_fee_usd(shares, ex, self.fee_rate())
        return round(fee, 4)

    def _normalize_legacy_fees(self) -> None:
        """One-time migration for state files written before fee accounting.

        Rewrites every settled record to pnl_gross / fee_est / pnl(net) and
        re-derives day_pnl_settled, W/L counters and the full-day aggregates
        on the net basis. profit_lock_baseline is re-derived as the NET PnL
        of legs settled at/before manual_resume_ts, preserving its meaning
        (day PnL at unlock) in the new caliber. Runs once: save() stamps
        fee_model afterwards. Legacy EARLY_EXIT legs get the entry fee only
        (no exit price was recorded for them).
        """
        if not self.settled:
            self.day_pnl_gross_all = 0.0
            self.day_fees_all = 0.0
            self.day_pnl_net_all = 0.0
            self.n_w_all = 0
            self.n_l_all = 0
            return
        before_pnl = float(self.day_pnl_settled)
        before_base = float(self.profit_lock_baseline or 0)
        g_all = f_all = 0.0
        w_all = l_all = 0
        streak = 0
        cutoff = float(self.manual_resume_ts or 0)
        base_net = 0.0
        for slug, meta in self.settled.items():
            if not isinstance(meta, dict):
                continue
            gross = float(meta.get("pnl_gross", meta.get("pnl") or 0) or 0)
            if "fee_est" in meta:
                fee = float(meta.get("fee_est") or 0)
            else:
                fee = self._estimate_fee(meta.get("cost_usd") or 0, meta.get("fill_px") or 0)
            net = round(gross - fee, 4)
            meta["pnl_gross"] = round(gross, 4)
            meta["fee_est"] = round(fee, 4)
            meta["pnl"] = net
            g_all += gross
            f_all += fee
            res = str(meta.get("result") or "")
            if res == "W":
                w_all += 1
                streak = 0
            elif res == "L":
                l_all += 1
                streak += 1
            ts = float(meta.get("ts") or 0)
            if cutoff > 0 and ts <= cutoff:
                base_net += net
        self.day_pnl_gross_all = round(g_all, 4)
        self.day_fees_all = round(f_all, 4)
        self.day_pnl_net_all = round(g_all - f_all, 4)
        self.n_w_all = w_all
        self.n_l_all = l_all
        self.day_pnl_settled = round(g_all - f_all, 4)
        self.n_w = w_all
        self.n_l = l_all
        self.consec_losses = streak
        if cutoff > 0:
            self.profit_lock_baseline = round(base_net, 4)
        log.warning(
            f"[DAXUE][FEE_NORMALIZE] legacy day_state migrated to net-of-fee: "
            f"day_pnl {before_pnl:+.4f} -> {self.day_pnl_settled:+.4f}; "
            f"baseline {before_base:+.4f} -> {float(self.profit_lock_baseline):+.4f}; "
            f"full-day gross={self.day_pnl_gross_all:+.4f} fees={self.day_fees_all:.4f} "
            f"net={self.day_pnl_net_all:+.4f} W/L(all)={w_all}/{l_all}"
        )

    def save(self) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": self.day,
            "tz": "Asia/Shanghai",
            "updated": time.time(),
            "day_pnl_settled": round(self.day_pnl_settled, 4),
            "consec_losses": self.consec_losses,
            "day_loss_stopped": self.day_loss_stopped,
            "lock_gains_stopped": self.lock_gains_stopped,
            "day_profit_stopped": self.day_profit_stopped,
            "profit_lock_baseline": float(self.profit_lock_baseline or 0),
            "manual_resume": bool(self.manual_resume),
            "manual_resume_ts": float(self.manual_resume_ts or 0),
            "pause_until": self.pause_until,
            "n_w": self.n_w,
            "n_l": self.n_l,
            "day_pnl_gross_all": round(self.day_pnl_gross_all, 4),
            "day_fees_all": round(self.day_fees_all, 4),
            "day_pnl_net_all": round(self.day_pnl_net_all, 4),
            "n_w_all": self.n_w_all,
            "n_l_all": self.n_l_all,
            "fee_model": "taker_fee_v1",
            "settled": self.settled,
            "pending": self.pending,
            "knobs": {
                "DAY_LOSS_STOP": self.config.get("DAY_LOSS_STOP"),
                "CONSEC_LOSS_PAUSE": self.config.get("CONSEC_LOSS_PAUSE"),
                "PAUSE_MIN": self.config.get("PAUSE_MIN"),
                "BIG_WIN_LOCK": self.config.get("BIG_WIN_LOCK"),
                "LOCK_GAINS": self.config.get("LOCK_GAINS"),
                "DAY_PROFIT_LOCK": self.config.get("DAY_PROFIT_LOCK"),
                "DAILY_TARGET_SOFT": self.config.get("DAILY_TARGET_SOFT"),
                "TAKER_FEE_RATE": self.fee_rate(),
            },
        }
        self.path().write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def rollover_if_needed(self) -> None:
        today = datetime.now(TZ_SH).strftime("%Y%m%d")
        if today == self.day:
            return
        try:
            self.save()
        except Exception:
            pass
        self.day = today
        self.day_pnl_settled = 0.0
        self.consec_losses = 0
        self.day_loss_stopped = False
        self.lock_gains_stopped = False
        self.day_profit_stopped = False
        self.profit_lock_baseline = 0.0
        self.manual_resume = False
        self.manual_resume_ts = 0.0
        self.pause_until = 0.0
        self.settled = {}
        self.pending = {}
        self.n_w = 0
        self.n_l = 0
        self.day_pnl_gross_all = 0.0
        self.day_fees_all = 0.0
        self.day_pnl_net_all = 0.0
        self.n_w_all = 0
        self.n_l_all = 0
        self.load()
        self.save()
        log.info(f"[DAXUE] day_state rollover → {self.day}")

    def provisional_pnl(self, open_positions: list) -> float:
        open_cost = 0.0
        for p in open_positions:
            if getattr(p, "live_filled", True):
                open_cost += float(getattr(p, "cost_usd", 0) or 0)
        return self.day_pnl_settled - open_cost

    def day_pnl_for_stop(self, open_positions: list) -> float:
        settled = self.day_pnl_settled
        prov = self.provisional_pnl(open_positions)
        return min(settled, prov)

    def record_settled(
        self,
        slug: str,
        side: str,
        result: str,
        pnl: Optional[float],
        fill_px: float = 0.0,
        decision_ask: float = 0.0,
        cost_usd: float = 0.0,
        exit_px: float = 0.0,
    ) -> None:
        if slug in self.settled:
            return
        pnl_v = float(pnl) if pnl is not None else 0.0
        fee_v = self._estimate_fee(cost_usd, fill_px, exit_px)
        net_v = round(pnl_v - fee_v, 4)
        self.settled[slug] = {
            "side": side,
            "result": result,
            "pnl": net_v,
            "pnl_gross": round(pnl_v, 4),
            "fee_est": fee_v,
            "fill_px": fill_px,
            "decision_ask": decision_ask,
            "cost_usd": cost_usd,
            "ts": time.time(),
        }
        self.pending.pop(slug, None)
        if result == "W":
            self.n_w += 1
            self.n_w_all += 1
            self.consec_losses = 0
            self.day_pnl_settled = round(self.day_pnl_settled + net_v, 4)
        elif result == "L":
            self.n_l += 1
            self.n_l_all += 1
            self.consec_losses += 1
            self.day_pnl_settled = round(self.day_pnl_settled + net_v, 4)
            pause_n = int(self.config.get("CONSEC_LOSS_PAUSE") or 0)
            if pause_n > 0 and self.consec_losses >= pause_n:
                mins = int(self.config.get("PAUSE_MIN") or 30)
                until = time.time() + mins * 60.0
                if until > self.pause_until:
                    self.pause_until = until
                until_s = datetime.fromtimestamp(self.pause_until, TZ_SH).strftime("%H:%M:%S")
                log.warning(
                    f"[DAXUE][CONSEC_LOSS_PAUSE] consec_losses={self.consec_losses}>="
                    f"{pause_n} pause_min={mins} until={until_s} Asia/Shanghai "
                    f"day_pnl_settled=${self.day_pnl_settled:+.2f}"
                )
        else:
            self.settled.pop(slug, None)
            return

        # Full-day aggregates: every settled leg (same totals, gross/fee/net split).
        self.day_pnl_gross_all = round(self.day_pnl_gross_all + pnl_v, 4)
        self.day_fees_all = round(self.day_fees_all + fee_v, 4)
        self.day_pnl_net_all = round(self.day_pnl_net_all + net_v, 4)

        # Big-win lock: one settled leg NET PnL > BIG_WIN_LOCK (not day-cumulative soft).
        if self.config.get("LOCK_GAINS") and not self.lock_gains_stopped:
            thr = float(self.config.get("BIG_WIN_LOCK") or 0)
            if thr > 0 and net_v > thr:
                self.lock_gains_stopped = True
                self.manual_resume = False
                log.warning(
                    f"[DAXUE][LOCK_GAINS] single-leg pnl=${net_v:+.2f}>"
                    f"BIG_WIN_LOCK=${thr:.2f} slug={slug} "
                    f"day_pnl_settled=${self.day_pnl_settled:+.2f} — "
                    f"stop new ENTRYs for the day (open holds settle)"
                )
        # Cumulative day-profit lock: (pnl − baseline) ≥ DAY_PROFIT_LOCK.
        self._maybe_trigger_day_profit_lock(slug=slug)
        self.save()

    def queue_pending(self, pos: Any) -> None:
        slug = getattr(pos, "slug", None) or (pos.get("slug") if isinstance(pos, dict) else None)
        if not slug or slug in self.settled:
            return
        if isinstance(pos, dict):
            meta = {
                "side": pos.get("side"),
                "cost_usd": float(pos.get("cost_usd") or 0),
                "fill_px": float(pos.get("fill_px") or 0),
                "decision_ask": float(pos.get("decision_ask") or pos.get("ask") or 0),
                "market_end": float(pos.get("market_end") or 0),
                "bad_fill": bool(pos.get("bad_fill")),
            }
        else:
            meta = {
                "side": pos.side,
                "cost_usd": float(pos.cost_usd),
                "fill_px": float(pos.fill_px or 0),
                "decision_ask": float(pos.decision_ask or pos.ask or 0),
                "market_end": float(pos.market_end or 0),
                "bad_fill": bool(pos.bad_fill),
            }
        self.pending[slug] = meta
        self.save()

    def try_settle_one(self, slug: str, meta: dict) -> Optional[str]:
        side = str(meta.get("side") or "")
        cost = float(meta.get("cost_usd") or self.config.get("POSITION_USD") or 2.0)
        fill = meta.get("fill_px")
        ask = meta.get("decision_ask")
        fill_f = float(fill) if fill not in (None, "", 0, 0.0) else None
        ask_f = float(ask) if ask not in (None, "", 0, 0.0) else None
        try:
            r = evaluate_position(
                slug=slug,
                side=side,
                cost_usd=cost,
                fill_px=fill_f,
                decision_ask=ask_f,
            )
        except Exception as e:
            log.warning(f"[DAXUE][SETTLE] gamma error slug={slug}: {e}")
            return "ERROR"
        if r.result in ("W", "L"):
            self.record_settled(
                slug=slug,
                side=side,
                result=r.result,
                pnl=r.pnl_usd,
                fill_px=float(fill_f or 0),
                decision_ask=float(ask_f or 0),
                cost_usd=cost,
            )
            rec = self.settled.get(slug) or {}
            log.info(
                f"[DAXUE][SETTLE] gamma {r.result} slug={slug} side={side} "
                f"pnl_gross={r.pnl_usd} fee_est={rec.get('fee_est')} "
                f"pnl_net={rec.get('pnl')} winner={r.winner} "
                f"day_pnl=${self.day_pnl_settled:+.2f} "
                f"day_all_net=${self.day_pnl_net_all:+.2f} "
                f"streak_L={self.consec_losses}"
            )
            return r.result
        return r.result or r.status or "PENDING"

    def retry_pending(self, max_n: int = 8) -> None:
        if not self.pending:
            return
        items = list(self.pending.items())[:max_n]
        for slug, meta in items:
            self.try_settle_one(slug, meta)

    def _maybe_trigger_day_profit_lock(self, slug: str = "") -> bool:
        """Set day_profit_stopped when (day_pnl_settled − baseline) ≥ DAY_PROFIT_LOCK.

        Sticky for the Asia/Shanghai day once triggered. After manual_unlock,
        profit_lock_baseline is set to day_pnl_settled so another full
        +DAY_PROFIT_LOCK of settled PnL is required before re-lock — not merely
        day_pnl ≥ threshold and day_pnl > baseline (which would re-lock on the
        next tiny win while already above $15).
        """
        if self.day_profit_stopped:
            return False
        thr = float(self.config.get("DAY_PROFIT_LOCK") or 0)
        if thr <= 0:
            return False
        baseline = float(self.profit_lock_baseline or 0)
        progress = float(self.day_pnl_settled) - baseline
        if progress < thr:
            return False
        self.day_profit_stopped = True
        # Hitting DAY_PROFIT after resume ends the manual resume window so a
        # later BIG_WIN recompute can also see new legs normally.
        if self.manual_resume:
            self.manual_resume = False
        log.warning(
            f"[DAXUE][DAY_PROFIT_LOCK] day_pnl_settled=${self.day_pnl_settled:+.2f} "
            f"baseline=${baseline:+.2f} progress=${progress:+.2f}>="
            f"DAY_PROFIT_LOCK=${thr:.2f}"
            f"{f' slug={slug}' if slug else ''} — "
            f"stop new ENTRYs for the day (open holds settle)"
        )
        return True

    def recompute_day_profit_lock(self, persist: bool = False) -> None:
        """Sticky DAY_PROFIT_LOCK from (day_pnl_settled − profit_lock_baseline)."""
        thr = float(self.config.get("DAY_PROFIT_LOCK") or 0)
        was = bool(self.day_profit_stopped)
        baseline = float(self.profit_lock_baseline or 0)
        progress = float(self.day_pnl_settled) - baseline
        if thr <= 0:
            now_locked = False
        elif was:
            # Sticky once on: later losses do not reopen the day.
            now_locked = True
        elif progress < thr:
            now_locked = False
        else:
            now_locked = True
        self.day_profit_stopped = now_locked
        if now_locked and self.manual_resume:
            self.manual_resume = False
        if now_locked and not was:
            log.warning(
                f"[DAXUE][DAY_PROFIT_LOCK] recompute ON "
                f"day_pnl_settled=${self.day_pnl_settled:+.2f} "
                f"baseline=${baseline:+.2f} progress=${progress:+.2f}>="
                f"DAY_PROFIT_LOCK=${thr:.2f}"
            )
        elif was and not now_locked:
            log.info(
                f"[DAXUE][DAY_PROFIT_LOCK] recompute OFF "
                f"day_pnl_settled=${self.day_pnl_settled:+.2f} "
                f"baseline=${baseline:+.2f} progress=${progress:+.2f} "
                f"thr=${thr:.2f}"
            )
        if persist and was != now_locked:
            try:
                self.save()
            except Exception as e:
                log.warning(f"[DAXUE] day_state save after day_profit recompute: {e}")

    def recompute_lock_gains(self, persist: bool = False) -> None:
        """Set lock_gains_stopped from BIG_WIN_LOCK vs settled legs (not soft target).

        After manual_unlock(), legs settled at/before manual_resume_ts are ignored
        so trading can resume; a later single-leg win > BIG_WIN_LOCK locks again.
        """
        enabled = bool(self.config.get("LOCK_GAINS"))
        thr = float(self.config.get("BIG_WIN_LOCK") or 0)
        hit_slug = None
        hit_pnl = 0.0
        cutoff = float(self.manual_resume_ts or 0) if self.manual_resume else None
        if enabled and thr > 0:
            for slug, meta in self.settled.items():
                try:
                    pnl = float((meta or {}).get("pnl") or 0)
                except (TypeError, ValueError):
                    continue
                if cutoff is not None:
                    try:
                        leg_ts = float((meta or {}).get("ts") or 0)
                    except (TypeError, ValueError):
                        leg_ts = 0.0
                    if leg_ts <= cutoff:
                        continue
                if pnl > thr:
                    hit_slug, hit_pnl = slug, pnl
                    break
        was = bool(self.lock_gains_stopped)
        now_locked = hit_slug is not None
        self.lock_gains_stopped = now_locked
        if now_locked and self.manual_resume:
            # A post-resume big win re-locks and ends the manual resume window.
            self.manual_resume = False
        if now_locked and not was:
            log.warning(
                f"[DAXUE][LOCK_GAINS] recompute ON slug={hit_slug} "
                f"pnl=${hit_pnl:+.2f}>BIG_WIN_LOCK=${thr:.2f} "
                f"day_pnl_settled=${self.day_pnl_settled:+.2f}"
            )
        elif was and not now_locked:
            log.info(
                f"[DAXUE][LOCK_GAINS] recompute OFF — no single-leg pnl>"
                f"BIG_WIN_LOCK=${thr:.2f} (legacy soft-target lock cleared"
                f"{' or manual_resume' if cutoff is not None else ''}) "
                f"day_pnl_settled=${self.day_pnl_settled:+.2f}"
            )
        elif now_locked and was:
            log.info(
                f"[DAXUE][LOCK_GAINS] recompute keep ON slug={hit_slug} "
                f"pnl=${hit_pnl:+.2f}>BIG_WIN_LOCK=${thr:.2f}"
            )
        if persist and was != now_locked:
            try:
                self.save()
            except Exception as e:
                log.warning(f"[DAXUE] day_state save after recompute: {e}")

    def manual_unlock(self, reason: str = "user") -> dict:
        """Clear day entry locks so new ENTRYs are allowed again (same Asia/Shanghai day).

        Keeps settled PnL / W-L history. Sets manual_resume so historical
        BIG_WIN_LOCK legs no longer re-lock via recompute. Sets
        profit_lock_baseline=day_pnl_settled so DAY_PROFIT_LOCK fully resets:
        another +DAY_PROFIT_LOCK of settled PnL relative to that baseline is
        required before re-lock (not merely day_pnl ≥ thr and day_pnl > baseline).
        Clears sticky DAY_LOSS_STOP and CONSEC pause.
        """
        self.rollover_if_needed()
        before = {
            "day_loss_stopped": bool(self.day_loss_stopped),
            "lock_gains_stopped": bool(self.lock_gains_stopped),
            "day_profit_stopped": bool(self.day_profit_stopped),
            "consec_losses": int(self.consec_losses),
            "pause_until": float(self.pause_until or 0),
            "manual_resume": bool(self.manual_resume),
            "day_pnl_settled": float(self.day_pnl_settled),
            "profit_lock_baseline": float(self.profit_lock_baseline or 0),
            "day_pnl_net_all": float(self.day_pnl_net_all),
            "day_fees_all": float(self.day_fees_all),
            "n_w_all": int(self.n_w_all),
            "n_l_all": int(self.n_l_all),
        }
        now = time.time()
        self.day_loss_stopped = False
        self.lock_gains_stopped = False
        self.day_profit_stopped = False
        self.profit_lock_baseline = float(self.day_pnl_settled)
        self.manual_resume = True
        self.manual_resume_ts = now
        self.consec_losses = 0
        self.pause_until = 0.0
        self.recompute_lock_gains(persist=False)
        self.recompute_day_profit_lock(persist=False)
        self.save()
        after = {
            "day_loss_stopped": bool(self.day_loss_stopped),
            "lock_gains_stopped": bool(self.lock_gains_stopped),
            "day_profit_stopped": bool(self.day_profit_stopped),
            "consec_losses": int(self.consec_losses),
            "pause_until": float(self.pause_until or 0),
            "manual_resume": bool(self.manual_resume),
            "manual_resume_ts": float(self.manual_resume_ts),
            "day_pnl_settled": float(self.day_pnl_settled),
            "profit_lock_baseline": float(self.profit_lock_baseline or 0),
            "day_pnl_net_all": float(self.day_pnl_net_all),
            "day_fees_all": float(self.day_fees_all),
            "n_w_all": int(self.n_w_all),
            "n_l_all": int(self.n_l_all),
            "entry_block": self.entry_block_reason([], now),
        }
        log.warning(
            f"[DAXUE][MANUAL_UNLOCK] reason={reason} before={before} after={after}"
        )
        return {"ok": True, "reason": reason, "before": before, "after": after}

    def check_day_loss(self, open_positions: list) -> None:
        stop_usd = float(self.config.get("DAY_LOSS_STOP") or 0)
        if stop_usd <= 0 or self.day_loss_stopped:
            return
        pnl = self.day_pnl_for_stop(open_positions)
        # After manual_unlock, profit_lock_baseline == day_pnl at unlock. Do not
        # re-lock on the same pre-unlock absolute loss; only further drawdown of
        # DAY_LOSS_STOP from that baseline re-arms the stop and ends resume.
        if self.manual_resume:
            baseline = float(self.profit_lock_baseline or 0)
            further = float(pnl) - baseline
            if further > -stop_usd:
                return
            self.manual_resume = False
            self.day_loss_stopped = True
            self.save()
            log.warning(
                f"[DAXUE][DAY_LOSS_STOP] post-resume further=${further:+.2f}<="
                f"-{stop_usd:.2f} day_pnl_for_stop=${pnl:+.2f} "
                f"baseline=${baseline:+.2f} settled=${self.day_pnl_settled:+.2f} "
                f"provisional=${self.provisional_pnl(open_positions):+.2f} — "
                f"stop new ENTRYs for the day (open holds settle)"
            )
            return
        if pnl <= -stop_usd:
            self.day_loss_stopped = True
            self.save()
            log.warning(
                f"[DAXUE][DAY_LOSS_STOP] day_pnl_for_stop=${pnl:+.2f}<="
                f"-{stop_usd:.2f} settled=${self.day_pnl_settled:+.2f} "
                f"provisional=${self.provisional_pnl(open_positions):+.2f} — "
                f"stop new ENTRYs for the day (open holds settle)"
            )

    def entry_block_reason(self, open_positions: list, now: float) -> Optional[str]:
        self.rollover_if_needed()
        self.check_day_loss(open_positions)
        if self.day_loss_stopped:
            return "DAY_LOSS_STOP"
        if self.lock_gains_stopped:
            return "LOCK_GAINS"
        if self.day_profit_stopped:
            return "DAY_PROFIT_LOCK"
        if self.pause_until > now:
            left_m = (self.pause_until - now) / 60.0
            return f"CONSEC_LOSS_PAUSE({left_m:.0f}m left)"
        return None
