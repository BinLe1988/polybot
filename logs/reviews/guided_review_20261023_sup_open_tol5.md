# Guided-live: tighten ONE filter — SR_SUPPORT_OPEN_TOL $10→$5

**Time:** 2026-10-23 ~19:24 Asia/Shanghai  
**Trigger:** User confirmed after #93–#97 review optional item: SUPPORT open-tol $10→$5 (#97 boundary full-loss + earlier #70/#77/#86/#87/#88 band).

## Decision
**Changed:** `SR_SUPPORT_OPEN_TOL` / Started `SR_SUP_OPEN_TOL` **10 → 5**  
**Why:** SUPPORT UP allowed when `spot >= open - tol`. At tol=$10, #97 entered exactly at open−$10 (81628 vs open 81638) and full-lost −$2.10. Same direction as earlier below-open SUPPORT losses (#70/#77/#86/#87/#88). Symmetric with existing `SR_RESISTANCE_OPEN_TOL=5`. One knob only.

## Implementation
- Backup: `btc5m_lag_observe.py.bak_20261023_192404_sup_open_tol5`
- Code default: `os.getenv("SR_SUPPORT_OPEN_TOL", "5")` (was `"10"`); Started/runtime fallbacks 10→5
- Restart env: `GUIDED_LIVE=1 GUIDED_AUTO=1 REVIEW_PAUSE_EVERY=5 MAX_RUN_SEC=3600 SR_SUPPORT_OPEN_TOL=5 SR_RESISTANCE_MAX_ASK=0.55` (+ standing SR flags explicit)
- Old PID **23975** (log `guided_live_20261023_190906.log`) → new PID **26102**
- Log: `logs/guided_live_20261023_192458.log`
- Started confirmed: `SR_RES_MAX=0.55 SR_SUP_MAX=0.55 SR_SUP_OPEN_TOL=5 SR_RES_OPEN_TOL=5 SR_SUP_DROP=40/180s SR_SUP_SKIP_TD=1 SR_SUP_TD_CD=60s POSITION_USD=2.0 REVIEW_PAUSE_EVERY=5`

## NOT changed
- SR_RES_MAX=0.55 / SR_SUP_MAX=0.55  
- SR_RESISTANCE_OPEN_TOL=5  
- SR_SUPPORT_SKIP_TREND_DOWN + 60s cooldown  
- SR_SUPPORT_DROP_USD=40 / 180s  
- Global ask band, lag rules, EDGE 0.06 log-only  
- POSITION $2, REVIEW_PAUSE_EVERY=5, MAX_RUN_SEC=3600  

## Suggested Chinese one-liner
只收紧支撑开盘容差 $10→$5（其它过滤不动），已重启实盘。
