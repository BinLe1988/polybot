# BTC 5m DAXUE 昨日复盘日汇总 2026-10-29（Asia/Shanghai）

- 结算口径：**Polymarket Gamma**（`daxue_edge_snapshot.py` / `btc5m_poly_settle`）；不以 next-window-open 作主 W/L
- 源：`logs/reviews/daxue_edge_20261029.md`（00:05 刷新）、`logs/daxue_day_state_20261029.json`（权威日末）、五份 `daxue_review_20261029_*.md`、`logs/daxue_live_20261029_*.log`（25 个）
- GUIDED_LIVE：**冻结**（本日不挖 guided 复盘）
- 目标分层：保本金 → 验边 → 再谈日收益

## 保本金

- **日结算 PnL：+$46.21**（23 笔全部已结算；`day_state.day_pnl_settled=46.2071`，9W/14L）
- **日亏熔断（DAY_LOSS_STOP=$7）**：**盘中曾触发一次**。04:24 在 settled≈−$5.04、provisional≈−$7.04 时打出 `DAY_LOSS_STOP`；随后 04:31 DOWN fill=0.18 单腿 **+$9.11** 把累计拉回正值。日末 `day_loss_stopped=false`，相对熔断垫约 **+$53.21**
- **连亏暂停（CONSEC_LOSS_PAUSE=3 / PAUSE_MIN=30）**：**实战命中 4 次**（日志 WARNING）：
  - 01:26 streak=3 → 暂停至 01:56（当时 settled≈−$2.44）
  - 03:37 streak=3 → 暂停至 04:07（settled≈−$5.04，贴近熔断）
  - 22:14 streak=3 → 暂停至 22:44（settled≈+$48.21）
  - 23:06 streak=4 → 暂停至 23:36（settled≈+$46.21）
  约 23:53 用户「重置吧」后 `daxue_manual_unlock` + 重启，baseline→46.21、consec 清零
- **单笔大赚锁盈（BIG_WIN_LOCK=$6 / LOCK_GAINS）**：**多次触发**（04:31 +$9.11、10:28 +$7.37、13:26 +$8.53、16:41 **+$31.33**）。同步 **DAY_PROFIT_LOCK=$15** 在 13:26、16:41 命中。日末因多次手动解锁 / `manual_resume=true`，`lock_gains_stopped=false`、`day_profit_stopped=false`——锁盈机制有打出，但未「锁死到收盘」
- **最大回撤（按 Gamma 结算顺序滚动）**：早盘谷底约 **−$5.04**（03:37，穿本约 $5）；日高约 **+$55.08**（16:43）；此后回吐至日末 **+$46.21**，**峰→末回撤约 $8.87**。全日最险是凌晨逼近 −$7 熔断线，午后大胜腿把本金层拉到很厚
- 仓位尺度未变：$2/腿、`max_deploy=$13`、`ask_band=[0.22,0.40]`；相对 bankroll 记账 $20，绝对盈利很大，但本层看重的是「凌晨熔断挡过一次、连亏暂停在用、大赚后有锁」

## 验边

- **成交笔数**：23（W=9 / L=14 / PENDING=0）——以 `day_state` 为准（edge md 有一行 Gamma ERROR，已在 day_state 记为 L −$2）
- **胜率 WR**：39.1%（9/23）
- **均成交价 mean_fill**：0.2508
- **WR−mean_fill**：**+0.140**
- **是否像市价随机**：不完全像。二元买价附近随机大致要求 WR≈fill 才打平；此处 WR 仍高于 mean_fill，**正 edge 信号还在**，但胜率本身 <50%，盈利结构更依赖少数低 fill 大胜腿（尤其 16:34 UP fill=0.06 → +$31.33），单日样本方差极大
- 结构观察：凌晨 3 连亏簇两次逼近/触发熔断；午后五连胜（复盘 16:54）把日 PnL 推到峰值；晚间 19:20 起连亏回吐约 $9，连亏暂停再次生效。便宜成交仍≠方向对（如 fill=0.05/0.11 的 L）
- 五笔复盘仅作过程切片；**以本文件 / day_state / edge snapshot 的 Gamma 为准**

## 相对日收益目标

- 「每天 2%–5%」仅作对照愿望值，**不是当前 KPI**
- 旧软目标 **$0.80（DAILY_TARGET_SOFT）已停用**；Started 行标 `DAILY_TARGET_SOFT=unused`；锁盈由 **BIG_WIN_LOCK>$6** 与 **DAY_PROFIT_LOCK=$15** 驱动
- 对照说明：在 $2/腿、日约 23 笔尺度下，+$46.21 远高于「小幅日涨」叙事；**不据此催促加仓或抬 `max_deploy`**——优先确认 edge 可重复，并消化「大胜腿 + 晚间回吐」的路径依赖

## 当日风控与参数变化

| 时段（约） | 变化 / 事件 |
|---|---|
| 全日 Started | `ask_band=[0.22,0.40]` / `ask_max=0.40`；`DAY_LOSS_STOP=$7`；`CONSEC_LOSS_PAUSE=3` / 30m；`BIG_WIN_LOCK=$6`；`DAY_PROFIT_LOCK=$15`；`DAILY_TARGET_SOFT=unused`；GUIDED 冻结；proxy 7897 |
| 01:26 / 03:37 | 两次 CONSEC 暂停；03:37 后 settled 探底 −$5.04 |
| 04:24 | **DAY_LOSS_STOP**（provisional −$7.04） |
| 04:31 | 单腿 +$9.11 → **LOCK_GAINS**；累计转正 |
| 08:41–16:43 | 在多次锁盈/日盈锁告警后仍有新成交（依赖重启与手动解锁路径）；峰值 +$55.08 |
| 13:26 / 16:41 | DAY_PROFIT_LOCK 命中；16:41 单腿 **+$31.33** |
| 19:20–23:06 | 晚间连亏回吐；22:14 / 23:06 再触发 CONSEC |
| ~23:53 | 用户重置：baseline→46.21、consec 清零、进程换 PID |
| 全天 | ~25 个 live log / 多次 `MAX_RUN_SEC=3600` 干净退出重启；**无 BAD_FILL 实锤**；geoblock HK / proxy 正常 |

## 未来建议（仍服务保本金 → 验边）

1. **保持** $2/腿与 `ASK_MAX=0.40`，先用更多交易日看 WR−mean_fill 是否在「没有超大胜腿」时仍为正；不为冲 2%–5% 日涨幅加仓
2. **正视** 凌晨熔断与连亏暂停已在干活——保留 `DAY_LOSS_STOP=$7` 与 `CONSEC_LOSS_PAUSE=3`；若晚间在高利润区仍频繁 3–4 连亏，可考虑高水位时略收紧暂停，而不是加仓回补
3. **理顺** 锁盈与手动解锁：BIG_WIN / DAY_PROFIT 多次命中后仍继续开仓，说明「锁」被人工/重启路径打开；若保本金优先，大胜后应默认锁到日末，解锁要显式、可审计
4. 复盘与监控继续 **只用 Gamma**；不把单笔 fill=0.06 级暴击当可复制 alpha
5. GUIDED 维持冻结，直到 DAXUE 边在更多「普通 fill」日子里站稳
