# BTC 5m 五笔复盘（历史补追）— 2026-10-21 16:52–17:50（Asia/Shanghai）

- **类型**：catch-up historical（归档补追，**非**实时复盘门闩）
- **漏触发原因**：`REVIEW_PAUSE_EVERY` 原按进程内 `stats.guided_entries` 计数；每小时 `MAX_RUN` 重启后计数归零，16:27 复盘后多笔成交从未在单进程内凑满 5 笔 → `[PAUSE][REVIEW]` 未触发
- **本批**：16:27 之后全局序列 **#1–#5**（早于已补追的 #13–#17）
- **会话日志**：
  - `guided_live_20261021_163324.log`（#1–#3）
  - `guided_live_20261021_173645.log`（#4–#5）
- **仓位**：每笔 amt=$2.00；`SR_RES_MAX=0.65`；lag/SUPPORT ask 带 `[0.22,0.78]`
- **暂停**：本批为归档补追 → **不暂停实盘**（`paused=false` 保持；**不**下调 `reviewed_lifetime_entries`）
- **姊妹报告**：
  - 上一正常复盘：`guided_review_20261021_1627.md`
  - 同日其它补追：`guided_review_20261021_catchup_1822_1931.md`、`guided_review_20261021_catchup_1940_2020.md`
  - 最新 5 笔补追（#13–17）：`guided_review_20261021_2215.md`

## 五笔明细

1. **16:52:32** lag **DOWN** @**0.650** slug=`btc-updown-5m-1792572600` amt=$2.00  
   - 无早退；持有结算。会话 guided pnl `0 → +1.03`（16:58 STATS）。约 **+$1.03**。  
   - 同期：多次 `ask-above-sr-max` / `ask-out-of-band` / FAK 无对手（未计入本笔）。

2. **17:02:52** lag **DOWN** @**0.380** slug=`btc-updown-5m-1792573200` amt=$2.00  
   - 无早退；持有结算。guided pnl `−1.05 → −3.14`（17:08，Resolved 2→3）。约 **−$2.09 全损**。  
   - 注：17:03 的 `−1.05` 含此前纸面 `[SIGNAL] UP @0.410`（未 LIVE 成交）结算亏损 ≈−$2.08，**计入会话 guided_pnl，但不属于本 LIVE 笔**。

3. **17:17:14** lag **UP** @**0.680** slug=`btc-updown-5m-1792574100` amt=$2.00  
   - 无 GUIDED 早退（17:23 有横盘 ALERT 但 `early_exits=0`）；持有结算。guided `−3.14 → −5.19`。约 **−$2.05 全损**。  
   - 会话 `163324` FINAL：entries=3 early_exits=0 pnl_net=**$−5.19**（含上述纸面信号扰动）。

4. **17:40:40** SUPPORT **UP** @**0.640** slug=`btc-updown-5m-1792575600` amt=$2.00（日志 `173645`）  
   - 无早退；持有结算。guided `0 → −2.05`（17:46）。约 **−$2.05 全损**。

5. **17:50:39** SUPPORT **UP** @**0.760** slug=`btc-updown-5m-1792576200` amt=$2.00  
   - **早退成功**：`[GUIDED][LIVE][EXIT] range_early UP bid=0.750 pnl_net=$-0.094` @17:56:27。约 **−$0.09**。  
   - 贵价支撑（0.76）几乎贴上限；早退仅吃到极薄价差。

**本批 LIVE 粗算合计**：+$1.03 −$2.09 −$2.05 −$2.05 −$0.09 ≈ **−$5.25**（不含纸面信号）。

## 观察

- **lag 三连**：一赢两全损；便宜腿 0.38 DOWN 与贵价 0.68 UP 均亏，说明 edge 纸面成立≠结算方向。
- **SR_RES_MAX=0.65** 在 `163324` 已生效（如 ask=0.70 被 `skip ask-above-sr-max`）。
- **SUPPORT @0.76** 偏贵：早退 −$0.09 优于扛到结算，但仍是负期望样本。
- **FAK / 流动性**：入场 FAK 失败多次；本批仅 #5 成功 early exit。
- **guided_pnl 混入纸面 SIGNAL**：STATS 的 `pnl_net` 在 `GUIDED_AUTO` 下会累加未 LIVE 的 lag 信号，归档时需按 ENTRY 行与 Resolved 步进拆开。

## 自动改动

无（历史归档；**不**改 CONFIG、**不**重启 bot、**不**动复盘门闩计数）。

## 待定 / 下次（仅建议，未落地）

- 是否对 **SUPPORT** 也加高价帽（例如 ask>0.70 或对齐阻力 0.65）——本批 #5 @0.76 是直接样本。
- lag 贵价腿（如 UP@0.68）是否收紧上限，与现有 band 0.22–0.78 一并观察。
- 执行层：FAK 无对手、SELL allowance（后续批次更明显）——非本批策略阈值。

## 交叉链接

见文首姊妹报告；lifetime 门闩修复见 `guided_review_20261021_2215.md`。
