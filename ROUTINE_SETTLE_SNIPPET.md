# Snippet for btc-5m routine UpdateRoutine (settlement)

Replace any instruction that settles W/L via「下一窗开盘 vs 本窗开盘 / next-open」
with the following intent (do not bake MCP tool schemas):

---

When writing a DAXUE 五笔复盘 (`logs/reviews/daxue_review_YYYYMMDD_HHMM.md`)
or reporting fill W/L / PnL:

1. On Mac mini `1734af6c-…`, in `/Users/nick/poly-test/`, run:
   `python3 daxue_five_trade_review.py --after '<last_review_time Asia/Shanghai>' --last 5 --md`
   (or `python3 btc5m_poly_settle.py resolve <slug> --side UP|DOWN --ask … --fill-px … --cost 2 --json`).
2. Use the helper’s **actual_winner / W/L / PnL / source=gamma**. If status is PENDING, mark PENDING — do not guess.
3. Review table must include: side, decision_ask, fill_px (if known), actual winner, W/L vs position, PnL from fill_px (else ask), source.
4. Never use next-window-open as primary W/L. Optional: note next-open vs window-open as **参考 only**.
5. See `SETTLEMENT.md`. Do not restart DAXUE unless required; GUIDED stays frozen; no live orders from the routine.

---

Deliver the check result to the user in this chat as today; if nothing new to report, say so briefly.
