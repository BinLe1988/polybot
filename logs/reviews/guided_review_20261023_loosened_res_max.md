# Guided-live: loosen ONE filter — SR_RES_MAX 0.50→0.55

**Time:** 2026-10-23 ~13:53 Asia/Shanghai  
**Trigger:** User asked to loosen one BTC 5m filter after ~2.5h drought (last fill 11:22 SUPPORT UP @0.470).

## Decision
**Changed:** `SR_RESISTANCE_MAX_ASK` / Started `SR_RES_MAX` **0.50 → 0.55**  
**Why (priority):** Drought window (~11:22→13:49) skips were dominated by global ask-out-of-band (must not loosen). Next actionable RES knob: several `ask-at-or-above-sr-res-max` (e.g. ask=0.500, 0.520) that a one-step 0.55 would admit, while keeping RES open-tol=$5 and SUP max=0.55. RES open-tol 5→10 was inferior for this dry spell (no above-open skips in window). SUP max loosen deferred (parent: only if RES max clearly inferior).

## Implementation
- Backup: `btc5m_lag_observe.py.bak_20261023_135313_res_max55`
- Code default: `os.getenv("SR_RESISTANCE_MAX_ASK", "0.55")` (was `"0.50"`)
- Restart env: `GUIDED_LIVE=1 GUIDED_AUTO=1 REVIEW_PAUSE_EVERY=5 MAX_RUN_SEC=3600 SR_RESISTANCE_MAX_ASK=0.55` (+ other SR flags unchanged, explicit)
- Old PID **96097** → new PID **99560**
- Log: `logs/guided_live_20261023_135434.log`
- Started confirmed: `SR_RES_MAX=0.55 SR_SUP_MAX=0.55 SR_SUP_OPEN_TOL=10 SR_RES_OPEN_TOL=5 SR_SUP_DROP=40/180s SR_SUP_SKIP_TD=1 SR_SUP_TD_CD=60s POSITION_USD=2.0`

## NOT changed
- SR_SUPPORT_MAX_ASK=0.55  
- SR_SUPPORT_OPEN_TOL=10 / SR_RESISTANCE_OPEN_TOL=5  
- SR_SUPPORT_SKIP_TREND_DOWN + 60s cooldown  
- SR_SUPPORT_DROP_USD=40 / 180s  
- Global ask band [0.22,0.78]  
- POSITION $2, REVIEW_PAUSE_EVERY=5, MAX_RUN_SEC=3600  
- No lag last-60s ban  

## Suggested Chinese one-liner
只放宽阻力位最高买价 0.50→0.55（其它过滤不动），已重启实盘。
