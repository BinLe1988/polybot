# Gamma vs next-open validation (2026-10-28 Asia/Shanghai)

Known mislabeled DAXUE legs: old reviews used next-window-open heuristic.

| 时间 | 方向 | slug | ask | fill | old(next-open) | actual winner | actual W/L | actual PnL | source | agree? |
|------|------|------|-----|------|----------------|---------------|------------|------------|--------|--------|
| 12:36 | UP | 1793162100 | 0.280 | - | W (+5.14) | DOWN | L | -2.00 | gamma | **NO** |
| 13:10 | DOWN | 1793164200 | 0.450 | - | W (+2.44) | UP | L | -2.00 | gamma | **NO** |
| 13:38 | UP | 1793165700 | 0.370 | - | W (+3.41) | DOWN | L | -2.00 | gamma | **NO** |
| 16:08 | DOWN | 1793174700 | 0.220 | 0.19 | W (+7.09) | UP | L | -2.00 | gamma | **NO** |
| 16:50 | UP | 1793177400 | 0.380 | 0.37 | W (+3.26) | DOWN | L | -2.00 | gamma | **NO** |

All five disagree: next-open marked W; Gamma marked L.

Helper: `btc5m_poly_settle.py` / `daxue_five_trade_review.py`. See SETTLEMENT.md.
