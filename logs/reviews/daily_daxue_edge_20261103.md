# BTC 5m DAXUE 昨日复盘日汇总 2026-11-03（Asia/Shanghai）

- 结算口径：**Polymarket Gamma**（`daxue_day_state` / `btc5m_poly_settle` / `daxue_edge_snapshot`）；不以 next-window-open 作主 W/L
- 源：`logs/daxue_day_state_20261103.json`（**权威日末**）、`logs/reviews/daxue_edge_20261103.md`（2026-11-04 00:11 刷新，n=3 与 day_state 一致）、`logs/reviews/daxue_review_20261103_2040.md`（20:40 过程切片，含跨日旧腿，**勿单独当全日**）、约 16 个 `logs/daxue_live_20261103_*.log`
- GUIDED_LIVE：**冻结**（本日不挖 guided 复盘）
- 目标分层：保本金 → 验边 → 再谈日收益

## 保本金

- **日结算 PnL：+$11.52**（3 笔全部 Gamma 已结算；`day_state.day_pnl_settled=11.5238`，**2W/1L**，`pending={}`）
- **日亏熔断（DAY_LOSS_STOP=$7）**：**未触发**（`day_loss_stopped=false`；edge `hit_stop=False`；相对 −$7 cushion≈**+$18.52**）
  - 说明： knobs / Started 行实际为 **$7.00**（非口头「默认 −$6」）；全日 settled 从未跌破 0
- **连亏暂停（CONSEC_LOSS_PAUSE=3 / PAUSE_MIN=30）**：**未触发**（日末 `consec_losses=1`；日志无 `[CONSEC_LOSS_PAUSE]` WARNING）
- **单笔大赚锁盈（BIG_WIN_LOCK=$6 / LOCK_GAINS）**：**触发一次**
  - **09:23:59** 第 1 笔 Gamma W **+$7.52**（fill=0.21）→ `[LOCK_GAINS] single-leg pnl=$+7.52>BIG_WIN_LOCK=$6.00`，`day_pnl_settled=$+7.52`，停止新 ENTRY；edge `max_leg=$+7.52 hit=True`
  - 随后多次 `MAX_RUN` 重启（09:43 / 12:02 / 14:09）Started 行均 `stopped=True`，并有 `[LOCK_GAINS] recompute keep ON` —— **本日锁盈跨重启持久化正常**（对比 11-01 曾丢锁）
  - **15:47:22** `manual_resume=true`（`profit_lock_baseline=7.5238`）；**15:48:35** 重启后 `stopped=False` 解锁继续交易
  - 第 2 笔单腿 PnL **恰好 +$6.00**（条件为 **>** $6）→ **未再次 LOCK_GAINS**；日末 `lock_gains_stopped=false`
- **DAY_PROFIT_LOCK=$15**：**未触发**（峰值 settled **+$13.52** < $15；`day_profit_stopped=false`）
- **最大回撤相关**：滚动 settled 路径 **$0 → +$7.52（锁）→ 解锁后 +$13.52（峰值）→ +$11.52（日末）**；自峰值回撤约 **$2.00**（仅第 3 笔 L −$2）。日末 `open_pos=0`、`consec_losses=1`、`manual_resume=true`
- 仓位尺度：全日 **$2/腿**；`ask_band=[0.22,0.40]` **未改**；`bankroll=$20` / `max_deploy=$13`；相对记账本金 +$11.52 ≈ **+58%**（样本极小，不据此扩仓）

### 三笔 Gamma 结算一览（fill 时刻 / 方向 / fill_px）

| # | 约 fill 时刻 | 方向 | ask | fill_px | PnL | slug |
|---|---|---|---|---|---|---|
| 1 | 09:17 | UP | 0.29 | **0.21** | **+7.52** | 1793668500 |
| 2 | 20:31 | UP | 0.26 | 0.25 | **+6.00** | 1793709000 |
| 3 | 22:25 | UP | 0.33 | 0.24 | −2.00 | 1793715900 |

## 验边

- **成交笔数**：3（W=2 / L=1 / PENDING=0）——`day_state` 与 edge snapshot 一致
- **胜率 WR**：**66.7%**（2/3）
- **均成交价 mean_fill**：**0.2333**
- **WR−mean_fill**：**+0.4333**
- **是否像市价随机**：**样本太小，不可当稳定 edge**。二元买价附近随机大致要求 WR≈fill 才打平；本日 WR=0.667 远高于 mean_fill≈0.233，差值约 **+43pp**，数字上极强，但 **n=3** 且赢腿依赖两笔很便宜的 fill（0.21 / 0.25），第一笔更是「彩票腿」驱动锁盈。**不能**据此判定可持续正 edge，更不能据此加仓
- 结构观察：上午一笔暴击锁盈 → 午后至夜前靠 `session_hours` + 锁仓几乎无成交 → 夜盘解锁后第二笔 W 推至峰值 +$13.52 → 第三笔 L 回吐 $2。20:40 五笔复盘混有跨日旧腿（净 +12.52 切片），**以本文件 / day_state / edge 的 Gamma 三笔为准**
- 日志**无**独立 `[BAD_FILL]` WARNING（相对 ask 买贵超 `bad_fill_over=0.03`）；第 1/3 笔 fill 相对 ask 实际更便宜（0.21 vs 0.29；0.24 vs 0.33）

## 相对日收益目标

- 「每天 2%–5%」仅作对照愿望值，**不是当前 KPI**
- 旧软目标 **$0.80（DAILY_TARGET_SOFT）已停用**；Started 行标 `DAILY_TARGET_SOFT=unused`；锁盈由 **BIG_WIN_LOCK>$6** 与 **DAY_PROFIT_LOCK=$15** 驱动（本日命中前者、未命中后者）
- 对照说明：在 $2/腿、日仅 3 笔尺度下，+$11.52 ≈ 记账 bankroll $20 的约 **+58%**，远超 2%–5% 愿望带；这是 **小样本暴击路径**，不是「日目标达标可加仓」的证据。**不据此催促加仓或抬 `max_deploy`**——优先继续观察 WR−mean_fill 在更多交易日是否仍非负，并守住锁盈/熔断纪律

## 当日风控与参数变化

| 时段（约） | 变化 / 事件 |
|---|---|
| 全日基线 | `ask_band=[0.22,0.40]` / `ask_max=0.40`；`POSITION_USD=$2`；`DAY_LOSS_STOP=$7`；`CONSEC_LOSS_PAUSE=3`/30m；`BIG_WIN_LOCK=$6`；`DAY_PROFIT_LOCK=$15`；`DAILY_TARGET_SOFT=unused`；GUIDED 冻结；proxy 7897；`early_exit=ON bid=[0.85,0.99]`；`adverse_slip>=0.08`；`late_reclaim=ON`；`trend_filter=OFF` |
| 09:03 | 进程启动；周末暂停时区初为 HKT；`overnight_pause=OFF`；`day_pnl=$0` `stopped=False` |
| 09:17 / 09:23 | 第 1 笔 FILL UP@0.21 → Gamma W +$7.52 → **LOCK_GAINS** |
| ~09:43 | **session_hours=ON**（周一12–16 / 周二–五08–16 + 夜 20–次日05 Asia/Shanghai）；周末暂停改 Shanghai；`stopped=True` 锁盈保持 |
| 10:43–15:09 | 多次 `MAX_RUN_SEC=3600` 干净停/启（约 **10** 次 MAX_RUN stop：10:43/13:01/15:09/16:48/18:02/19:07/20:08/21:09/22:12/23:20）；锁仓段 Started 持续 `stopped=True` |
| **15:47** | **manual_resume** 解锁（baseline=$+7.52）；15:48 重启 `stopped=False` |
| 16:00 起 | 日盘尾 `SKIP session_hours`（至夜盘 20:00 前） |
| 20:09 起 | 夜盘开放；`stopped=False` |
| 20:28 | `ask_above_max_price` SKIP（DOWN ask0.37） |
| 20:31 / 20:41 | 第 2 笔 FILL UP@0.25 → Gamma W +$6 → settled **+$13.52**；同窗曾有 adverse-slip SKIP 后才成交；**未再锁盈**（单腿 =$6 非 >$6） |
| 22:25 / 22:36 | 第 3 笔 FILL UP@0.24 → Gamma L −$2 → settled **+$11.52** streak_L=1 |
| 23:17 | `ask_above_max_price` SKIP（UP ask0.31）+ 另有 adverse-slip SKIP |
| 全天 | ask_band / POSITION_USD **未改**；无 DAY_LOSS / CONSEC / BAD_FILL；GUIDED 始终冻结 |

## 未来建议（仍服务保本金 → 验边）

1. **保持** $2/腿与 `ASK_MAX=0.40`；本日 WR−mean_fill 虽大幅为正，但 n=3 且两赢腿偏便宜 fill，**禁止**为冲 2%–5% 日涨幅或「复制 +58%」加仓
2. **保留** `BIG_WIN_LOCK=$6` 与跨重启 `LOCK_GAINS` 持久化——本日 09:23→15:47 锁仓段证明修复有效；同时注意 **恰好 =$6 的赢腿不会再锁**（第 2 笔），若要「峰值后再冻」，需接受显式阈值或改阈，而不是靠加仓对冲回吐
3. **保留** `DAY_LOSS_STOP=$7` 与 `CONSEC_LOSS_PAUSE=3`——本日未触达，仍是保本金底线；解锁后已用掉一个 −$2 回吐额度
4. **保留** `session_hours` + `late_reclaim` + `adverse_slip`——过滤噪音 ENTRY；继续用 Gamma 复盘，不把 next-open 当 W/L
5. 复盘继续 **只用 Gamma**；便宜赢腿（0.21）是小样本噪声/彩票腿，不把「买得便宜」当可持续 edge；先积累更多非锁盈日的 WR−mean_fill，再谈任何仓位尺度
6. GUIDED 维持冻结，直到 DAXUE 在更多日子里出现可持续的非负 WR−mean_fill，而不是依赖单笔 BIG_WIN 暴击后的日内路径
