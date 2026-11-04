# BTC 5m DAXUE 昨日复盘日汇总 2026-11-01（Asia/Shanghai）

- 结算口径：**Polymarket Gamma**（`daxue_day_state` / `btc5m_poly_settle` / `daxue_edge_snapshot`）；不以 next-window-open 作主 W/L
- 源：`logs/daxue_day_state_20261101.json`（**权威日末**）、`logs/reviews/daxue_edge_20261101.md`（2026-11-02 00:07 刷新，n=9 与 day_state 一致）、两份 `daxue_review_20261101_*.md`（08:51 / 17:36 过程切片；**勿单独当全日**）、约 30 个 `logs/daxue_live_20261101_*.log`
- GUIDED_LIVE：**冻结**（本日不挖 guided 复盘）
- 目标分层：保本金 → 验边 → 再谈日收益

## 保本金

- **日结算 PnL：−$3.78**（9 笔全部 Gamma 已结算；`day_state.day_pnl_settled=-3.7809`，**2W/7L**，`pending={}`）
- **日亏熔断（DAY_LOSS_STOP=$7）**：**触发一次（相对锁盈基线回撤）**；日末绝对 settled=−$3.78 **未**跌破 −$7，故 edge 标 `hit_day_loss_stop=False`，但 live 曾锁仓
  - **12:47:24** 单腿 W +$7.09 后 `LOCK_GAINS`，当时 settled≈**+$8.22**，基线记为 +$8.22
  - **20:28:33** 第 7 笔 FILL 后：`[DAY_LOSS_STOP] post-resume further=$-8.00<=-7.00`（`baseline=$+8.22` → `provisional=$+0.22`）→ 停止新 ENTRY；开仓持有至结算
  - 日末 `day_loss_stopped=false`（因随后 **manual_resume**）；相对绝对熔断线 cushion≈**+$3.22**（−3.78 vs −7.00）
- **连亏暂停（CONSEC_LOSS_PAUSE=3 / PAUSE_MIN=30）**：**2 次 WARNING**
  - **17:28:47** streak=3 → 暂停至 17:58:47（settled≈+$2.22；午后 15:38/16:26/17:22 三连 L）
  - **20:34:31** streak=4 → 暂停至 21:04:31（settled≈+$0.22；已叠 DAY_LOSS_STOP，以熔断为主）
- **单笔大赚锁盈（BIG_WIN_LOCK=$6 / LOCK_GAINS）**：**触发**（`max_leg=$+7.09` @12:38 DOWN fill=0.22；12:47 WARNING；日末 edge `hit_big_win_lock=True`）
  - **重要事实**：12:47 锁盈后约 **13:31 MAX_RUN 重启**起 Started 行长期 `stopped=False`，**13:00 后无任何 `SKIP LOCK_GAINS`**，午后仍成交 15:38/16:26/17:22（及 20:28）；**20:56** 才 `LOCK_GAINS recompute ON`。锁盈状态曾在重启后未挡住新开——保本金层面这是本日最大流程瑕疵
- **DAY_PROFIT_LOCK=$15**：**未触发**
- **最大回撤相关**：滚动 settled 峰值 **+$8.22**（12:38/12:47 第 3 笔 W 后），日末谷底 **−$3.78**；自峰值回撤约 **$12.00**。日末 `open_pos=0`、`consec_losses=2`、`manual_resume=true`（`manual_resume_ts`≈**22:10:37** Asia/Shanghai）
- 仓位尺度：全日 **$2/腿**；`ask_band=[0.22,0.40]` **未改**；`bankroll=$20` / `max_deploy=$13`；相对记账本金，日亏 −$3.78 ≈ −19%，**未**打满绝对 −$7 熔断，但锁盈后回吐超过一个熔断额度（基线口径）

### 九笔 Gamma 结算一览（fill 时刻 / 方向 / fill_px）

| # | 约 fill 时刻 | 方向 | ask | fill_px | PnL | slug |
|---|---|---|---|---|---|---|
| 1 | 08:38 | DOWN | 0.38 | 0.39 | **+3.13** | 1793493300 |
| 2 | 10:12 | DOWN | 0.38 | 0.36 | −2.00 | 1793499000 |
| 3 | 12:38 | DOWN | 0.35 | **0.22** | **+7.09** | 1793507700 |
| 4 | 15:38 | UP | 0.32 | 0.33 | −2.00 | 1793518500 |
| 5 | 16:26 | DOWN | 0.40 | 0.36 | −2.00 | 1793521500 |
| 6 | 17:22 | UP | 0.39 | 0.26 | −2.00 | 1793524800 |
| 7 | 20:28 | DOWN | 0.22 | 0.21 | −2.00 | 1793535900 |
| 8 | 23:26 | DOWN | 0.36 | 0.33 | −2.00 | 1793546700 |
| 9 | 23:41 | UP | 0.36 | 0.36 | −2.00 | 1793547600 |

## 验边

- **成交笔数**：9（W=2 / L=7 / PENDING=0）——`day_state` 与 edge snapshot 一致
- **胜率 WR**：**22.2%**（2/9）
- **均成交价 mean_fill**：**0.3133**
- **WR−mean_fill**：**−0.0911**
- **是否像市价随机**：**明显偏负 edge**。二元买价附近随机大致要求 WR≈fill 才打平；本日 WR=0.22 低于 mean_fill≈0.31，差距约 **9pp**，**不像正 edge**，更接近「偶发暴击 + 多数亏腿」。两笔赢腿：一笔偏贵 fill 0.39（+3.13），一笔很便宜 fill 0.22（+7.09，驱动全日峰值与锁盈）；其后 **6 连 L**（含解锁后夜间 2 笔）
- 结构观察：上午 08:38–12:38 爬升至 +$8.22；锁盈本应冻结，但重启后午后继续开火并三连亏至 +$2.22（CONSEC）；20:28 再亏触基线 DAY_LOSS；22:11 手动解锁后 23:26/23:41 再亏两笔至 −$3.78。五笔复盘仅为过程切片，**以本文件 / day_state / edge 的 Gamma 为准**
- 日志**无**独立 `[BAD_FILL]` WARNING（相对 ask 买贵超 `bad_fill_over=0.03`）

## 相对日收益目标

- 「每天 2%–5%」仅作对照愿望值，**不是当前 KPI**
- 旧软目标 **$0.80（DAILY_TARGET_SOFT）已停用**；Started 行标 `DAILY_TARGET_SOFT=unused`；锁盈由 **BIG_WIN_LOCK>$6** 与 **DAY_PROFIT_LOCK=$15** 驱动（本日命中前者、未命中后者）
- 对照说明：在 $2/腿、日 9 笔尺度下，−$3.78 ≈ 记账 bankroll $20 的约 −19%；盘中曾到 +$8.22 后几乎全部回吐。**不据此催促加仓或抬 `max_deploy`**——优先修复锁盈跨重启持久化，并把 WR−mean_fill 拉回非负，而不是用仓位回补日收益

## 当日风控与参数变化

| 时段（约） | 变化 / 事件 |
|---|---|
| 全日基线 | `ask_band=[0.22,0.40]` / `ask_max=0.40`；`POSITION_USD=$2`；`DAY_LOSS_STOP=$7`；`CONSEC_LOSS_PAUSE=3`/30m；`BIG_WIN_LOCK=$6`；`DAY_PROFIT_LOCK=$15`；`DAILY_TARGET_SOFT=unused`；GUIDED 冻结；proxy 7897；`overnight_pause=ON 00–08` |
| 00:22–~08:00 | overnight 时段；多次 `MAX_RUN_SEC=3600` 干净停/启（约 **17** 次 MAX_RUN stop）；无成交 |
| 08:38 / 10:12 | 第 1–2 笔：W +3.13 → L −2 → settled ≈+$1.13 |
| 12:38–12:47 | 第 3 笔 W +7.09 → settled ≈+$8.22 → **LOCK_GAINS**；随后 SKIP LOCK_GAINS 至 ~12:54 MAX_RUN 停 |
| 13:31 起 | 重启后 `stopped=False`，**锁盈未继续挡 ENTRY**（关键瑕疵） |
| 15:38–17:22 | 第 4–6 笔三连 L → +$2.22；**17:28 CONSEC_LOSS_PAUSE** 至 17:58 |
| ~18:02 | **TREND_FILTER=ON**（Started 起标注） |
| 20:28–20:34 | 第 7 笔 L → **DAY_LOSS_STOP**（相对基线 +8.22 回撤 $8）+ **CONSEC** streak=4 |
| ~20:56–20:57 | `adverse_slip` **0.10→0.08**；`stale_cheap` fill_max **0.25→0.27**；`entry_stable_ticks=3`；`LOCK_GAINS recompute ON`；仍 `stopped=True` |
| ~21:33 | **EARLY_EXIT ON** bid=[0.60,0.80] FOK_min≥0.60（日仍锁仓） |
| ~22:11 | **manual_resume 解锁**（baseline/profit_lock 语境下恢复交易）；cadence 回 */15；EARLY_EXIT 仍 [0.60,0.80]；TREND 仍 ON |
| ~23:00 | EARLY_EXIT bid **[0.60,0.80]→[0.85,0.99]**（FOK_min≥0.85） |
| ~23:24 | **TREND_FILTER OFF**（提高成交频率） |
| 23:26 / 23:41 | 第 8–9 笔均 Gamma L −$2 → settled **−$3.78** |
| ~23:49 | **LATE_RECLAIM_SKIP ON**（rem&lt;120s skip mid-path side）；重启 |
| 全天 | ask_band **未改**；无独立 BAD_FILL；GUIDED 始终冻结 |

## 未来建议（仍服务保本金 → 验边）

1. **优先修复** `LOCK_GAINS` / `day_loss_stopped` 跨 `MAX_RUN` 重启的持久化——本日若锁盈在 12:47 后真正冻住，可避免午后三连亏与后续基线熔断/解锁再亏；这比任何加仓更能保本金
2. **保持** $2/腿与 `ASK_MAX=0.40`；WR−mean_fill≈−0.09，**禁止**为冲 2%–5% 日涨幅或「回本」加仓
3. **保留** `DAY_LOSS_STOP=$7` 与 `BIG_WIN_LOCK=$6`——本日证明单笔暴击后回撤可以很快吃掉整天；解锁需显式接受「再亏一个熔断额度」
4. **保留** `CONSEC_LOSS_PAUSE=3`——17:28 的 30 分钟暂停有效；与熔断叠用时以熔断为主
5. 复盘继续 **只用 Gamma**；便宜赢腿（0.22）是样本噪声/彩票腿，不把「买得便宜」当可持续 edge；overnight / adverse-slip / entry_stable / late_reclaim 已就位，先观察其对 WR−mean_fill 的过滤，而不是扩仓
6. GUIDED 维持冻结，直到 DAXUE 在更多日子里出现可持续的非负 WR−mean_fill，而不是依赖单笔 BIG_WIN 暴击后的日内路径

