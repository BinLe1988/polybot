# Guided-live: add lag high-ask skip — LAG_MAX_ASK ≥0.60

**Time:** 2026-10-23 ~22:38 Asia/Shanghai  
**Trigger:** User confirmed after review: add lag high-ask skip when ask ≥ 0.60 (both UP and DOWN lag). Recent example: lag DOWN @ 0.650 (#session fill ~22:30) full-lost.

## Decision
**Changed:** new env/CONFIG `LAG_MAX_ASK` default **0.60**; guided lag entries skip when `ask >= LAG_MAX_ASK`.  
**Log tag:** `[GUIDED][LIVE|PAPER][ENTRY] lag {UP|DOWN} skip ask-at-or-above-lag-max ask=… lag_max_ask=0.60 …`  
**Started:** prints `LAG_MAX_ASK=0.60`  
**Why:** Mirror SR `ask-at-or-above-sr-*-max` pattern for lag only. Naming matches existing `LAG_*` (`LAG_EDGE_LOG_THRESHOLD`), not `SR_*`.

## Implementation
- Backup: `btc5m_lag_observe.py.bak_20261023_223805_lag_max_ask60`
- Code: CONFIG + Started + guided lag entry gate (UP and DOWN)
- Restart env: `GUIDED_LIVE=1 GUIDED_AUTO=1 REVIEW_PAUSE_EVERY=5 MAX_RUN_SEC=3600 LAG_MAX_ASK=0.60` (+ standing SR defaults via code/.env)
- Old PID **36536** already exited cleanly at MAX_RUN_SEC (log `guided_live_20261023_213609.log`) — no kill needed
- New PID **41836**
- Log: `logs/guided_live_20261023_223912.log`
- Started confirmed: `LAG_MAX_ASK=0.60` `SR_RES_MAX=0.55` `SR_SUP_MAX=0.55` `SR_SUP_OPEN_TOL=5` `SR_RES_OPEN_TOL=5` `SR_SUP_DROP=40/180s` `SR_SUP_SKIP_TD=1` `SR_SUP_TD_CD=60s` `POSITION_USD=2.0` `REVIEW_PAUSE_EVERY=5`

## NOT changed
- SR_RES_MAX=0.55 / SR_SUP_MAX=0.55  
- SR_SUP_OPEN_TOL=5 / SR_RES_OPEN_TOL=5  
- drop-momentum 40/180s  
- trend-DOWN skip + 60s cooldown  
- Global ask band [0.22, 0.78]  
- LAG_EDGE_LOG=0.06 (still log-only)  
- POSITION $2, REVIEW_PAUSE_EVERY=5, MAX_RUN_SEC=3600  

## Suggested Chinese one-liner
已加 lag 高 ask 帽 ≥0.60，已重启实盘。
