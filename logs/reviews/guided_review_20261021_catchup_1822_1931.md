# BTC 5m 五笔复盘（历史补追）— 2026-10-21 18:22–19:31（Asia/Shanghai）

- **类型**：catch-up historical（归档补追，**非**实时复盘门闩）
- **漏触发原因**：同批 A——`MAX_RUN` 重启重置进程内成交计数，未能触发 `[PAUSE][REVIEW]`
- **本批**：16:27 之后全局序列 **#6–#10**
- **会话日志**：
  - `guided_live_20261021_173645.log`（#6–#7）
  - `guided_live_20261021_185342.log`（#8–#10）
- **仓位**：amt=$2.00；`SR_RES_MAX=0.65`；lag/SUPPORT band `[0.22,0.78]`
- **暂停**：归档补追 → **不暂停实盘**；**不**下调 `reviewed_lifetime_entries`
- **姊妹报告**：
  - `guided_review_20261021_catchup_1652_1750.md`（#1–5）
  - `guided_review_20261021_catchup_1940_2020.md`（#11–12，不足5笔）
  - `guided_review_20261021_1627.md` / `guided_review_20261021_2215.md`

## 五笔明细

1. **18:22:10** SUPPORT **UP** @**0.640** slug=`btc-updown-5m-1792578000` amt=$2.00  
   - 无早退；持有结算。guided `−2.14 → −1.07`（18:27）。约 **+$1.07**。

2. **18:31:19** SUPPORT **UP** @**0.440** slug=`btc-updown-5m-1792578600` amt=$2.00  
   - 18:33:26 横盘 ALERT 后 **SELL 失败**：`not enough balance / allowance`（balance 3921569 < order 4540000）→ **hold to resolve**。  
   - 结算：FINAL guided `−3.17 → −0.70` 中本笔约 **+$2.47**（便宜支撑腿赔付厚）。  
   - 注：`−1.07 → −3.17` 含纸面 `[SIGNAL] UP @0.280`（18:29，未 LIVE）≈−$2.10，**非本笔**。  
   - 会话 `173645` FINAL：entries=4 early_exits=1 pnl_net=**$−0.70**（含 #4–#5 与纸面）。

3. **19:17:49** SUPPORT **UP** @**0.500** slug=`btc-updown-5m-1792581300` amt=$2.00（日志 `185342`）  
   - 19:22:26 **SELL 失败**：`No resting liquidity.` → hold to resolve。  
   - 结算后 guided `0 → +1.93`。约 **+$1.93**。

4. **19:22:55** SUPPORT **UP** @**0.610** slug=`btc-updown-5m-1792581600` amt=$2.00  
   - 19:27:10 **SELL 失败**：`not enough balance / allowance`（balance: 0）→ hold to resolve。  
   - 结算：guided `+1.93 → +3.15`。约 **+$1.22**。

5. **19:31:33** RESISTANCE **DOWN** @**0.360** slug=`btc-updown-5m-1792582200` amt=$2.00  
   - ask=0.36 ≤ `SR_RES_MAX=0.65`，合法入场；无早退；持有结算。  
   - guided `+3.15 → +6.62`（19:38）。约 **+$3.47**。  
   - 便宜阻力腿贡献本批最大单笔盈利。

**本批 LIVE 粗算合计**：+$1.07 +$2.47 +$1.93 +$1.22 +$3.47 ≈ **+$10.16**。

## 观察

- **与批 A 反差大**：五笔全胜（按 guided 结算归因）；便宜腿（SUPPORT 0.44 / RESISTANCE 0.36）赔付突出。
- **早退通道几乎全堵**：#7/#8/#9 三次 SELL 失败（allowance / 无对手），被迫 hold；本批反而因方向对而赢，但执行风险仍在。
- **SR_RES_MAX**：会话内仍见 `skip ask-above-sr-max`（如 0.68、0.73）；本笔阻力 0.36 未触帽。
- **SUPPORT 连续**：4/5 为支撑 UP，胜率好于批 A；中等价位（0.50–0.64）表现稳定。

## 自动改动

无（历史归档 only）。

## 待定 / 下次（仅建议，未落地）

- **SELL / allowance**：余额微短或 balance=0 导致无法早退——优先查 CLOB collateral / share allowance 与下单 size 取整；属执行层。
- 保持 **RESISTANCE skip ask>0.65**；本批便宜阻力验证「不追贵价阻力」方向正确。
- SUPPORT 高价帽仍可观察（本批无 ≥0.70 成交）。

## 交叉链接

见文首；批 C 承接本会话末笔 #11。
