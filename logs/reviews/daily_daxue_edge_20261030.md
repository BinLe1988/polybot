# BTC 5m DAXUE 昨日复盘日汇总 2026-10-30（Asia/Shanghai）

- 结算口径：**Polymarket Gamma**（`daxue_day_state` / `btc5m_poly_settle`）；不以 next-window-open 作主 W/L
- 源：`logs/daxue_day_state_20261030.json`（**权威日末**）、`logs/reviews/daxue_edge_20261030.md`（00:09 刷新，仅覆盖午后 4 笔，不完整）、三份 `daxue_review_20261030_*.md`、跨日 live 日志（含 `daxue_live_20261029_235252.log` 凌晨成交 + 17 个 `daxue_live_20261030_*.log`）
- GUIDED_LIVE：**冻结**（本日不挖 guided 复盘）
- 目标分层：保本金 → 验边 → 再谈日收益

## 保本金

- **日结算 PnL：−$16.00**（8 笔全部已结算；`day_state.day_pnl_settled=-16`，**0W/8L**，`pending={}`）
- **日亏熔断（DAY_LOSS_STOP=$7）**：**触发两次**，日末仍锁死
  - **00:45** 首触：`day_pnl_for_stop=−$8`（settled≈−$4 + provisional 开仓）→ `day_loss_stopped=true`；随后 00:55 第四笔结算后 settled=**−$8**，凌晨～傍晚长时间 SKIP
  - **~18:06 手动解锁**（`manual_resume`，`profit_lock_baseline=−8`，Started 显示 `stopped=False` streak 清零）后继续交易
  - **22:08** 再触：`post-resume further=−$8<=−$7`，`day_pnl_for_stop=−$16`（baseline=−8，settled 当时 −$14 + provisional → −$16）→ 再次锁仓；日末 `manual_resume=false`
- **连亏暂停（CONSEC_LOSS_PAUSE=3 / PAUSE_MIN=30）**：**至少 3 次 WARNING**
  - 00:49 streak=3 → 暂停至 01:19（settled≈−$6）
  - 00:55 streak=4 → 暂停至 01:25（settled=−$8；与日亏熔断叠加以熔断为主）
  - 21:13 streak=3 → 暂停至 21:43（settled=−$14；解锁后午后连亏）
  - 22:15 streak=4 → 暂停至 22:45（settled=−$16；已再锁日亏）
- **单笔大赚锁盈（BIG_WIN_LOCK=$6 / LOCK_GAINS）**：**未触发**（全日每腿均为 −$2，`max_leg=−$2`；`lock_gains_stopped=false`）
- **DAY_PROFIT_LOCK=$15**：**未触发**（`day_profit_stopped=false`）
- **最大回撤相关**：无日无盈利腿，滚动 settled 从 0 单调下行至 **−$16**；相对默认熔断线 −$7，最终穿透约 **$9**（因中途手动解锁后允许再亏一个「熔断额度」相对 baseline −$8）。日末 `open_pos=0`
- 仓位尺度未变：$2/腿、`max_deploy=$13`、`ask_band=[0.22,0.40]`；相对 bankroll 记账 $20，本层结论是**熔断有挡凌晨加仓，但手动解锁后资本层再次受损**

### 八笔 Gamma 结算一览（fill 时刻 / 方向 / fill_px）

| # | 约 fill 时刻 | 方向 | ask | fill_px | PnL | slug |
|---|---|---|---|---|---|---|
| 1 | 00:06 | DOWN | 0.38 | 0.32 | −2 | 1793289900 |
| 2 | 00:10 | UP | 0.40 | 0.38 | −2 | 1793290200 |
| 3 | 00:44 | DOWN | 0.30 | **0.022747** | −2 | 1793292000 |
| 4 | 00:45 | UP | 0.36 | 0.32 | −2 | 1793292300 |
| 5 | 19:07 | DOWN | 0.33 | 0.33 | −2 | 1793358300 |
| 6 | 20:27 | UP | 0.40 | 0.37 | −2 | 1793363100 |
| 7 | 21:08 | UP | 0.35 | 0.29 | −2 | 1793365500 |
| 8 | 22:08 | DOWN | 0.36 | 0.30 | −2 | 1793369100 |

## 验边

- **成交笔数**：8（W=0 / L=8 / PENDING=0）——以 `day_state` 为准（edge snapshot 仅列出午后 4 笔，**勿单独当全日**）
- **胜率 WR**：**0.0%**（0/8）
- **均成交价 mean_fill**：**0.2916**（含 00:44 异常低价 0.022747）；剔除该笔后 mean≈**0.3300**
- **WR−mean_fill**：**−0.2916**（剔除异常低价后约 **−0.330**）
- **是否像市价随机**：**不像，且明显更差**。二元买价附近随机大致要求 WR≈fill 才打平；本日 WR=0 远低于 mean_fill≈0.29–0.33，**负 edge / 方向全错样本**。便宜成交也救不了方向（00:44 fill≈0.023 仍 L −$2）
- 结构观察：凌晨 4 连亏触熔断；解锁后晚间再 4 连亏再锁。五笔复盘为过程切片（含跨日前笔），**以本文件 / day_state 的 Gamma 为准**
- 日志无独立 `[BAD_FILL]` WARNING（`bad_fill_over=0.03` 指相对 ask 买贵）；00:44 为**异常低 fill**，仍计 L

## 相对日收益目标

- 「每天 2%–5%」仅作对照愿望值，**不是当前 KPI**
- 旧软目标 **$0.80（DAILY_TARGET_SOFT）已停用**；Started 行标 `DAILY_TARGET_SOFT=unused`；锁盈由 **BIG_WIN_LOCK>$6** 与 **DAY_PROFIT_LOCK=$15** 驱动（本日均未命中）
- 对照说明：在 $2/腿、日 8 笔尺度下，−$16 ≈ 记账 bankroll $20 的 −80%，远差于「小幅日涨」叙事；**不据此催促加仓或抬 `max_deploy`**——优先保本金与搞清楚为何 0 胜，而不是用仓位回补

## 当日风控与参数变化

| 时段（约） | 变化 / 事件 |
|---|---|
| 全日 Started | `ask_band=[0.22,0.40]` / `ask_max=0.40`；`DAY_LOSS_STOP=$7`；`CONSEC_LOSS_PAUSE=3` / 30m；`BIG_WIN_LOCK=$6`；`DAY_PROFIT_LOCK=$15`；`DAILY_TARGET_SOFT=unused`；GUIDED 冻结；proxy 7897 |
| 00:06–00:45 | 凌晨 4 笔全 L；00:45 **DAY_LOSS_STOP**；00:49/00:55 **CONSEC** |
| 00:55–~18:05 | 熔断锁定，多次 `MAX_RUN_SEC=3600` 干净重启后仍 `stopped=True`，无新成交 |
| ~18:06 | **手动解锁**（baseline=−8，streak 清零，`stopped=False`） |
| 19:07–21:08 | 解锁后 3 笔再 L → settled −$14；21:13 **CONSEC** 至 21:43 |
| 22:08 | 第 8 笔 L → **再次 DAY_LOSS_STOP**（for_stop −$16）；22:15 CONSEC；用户侧倾向继续锁着 |
| 22:41 | 末次 `MAX_RUN` 干净退出，进程停；日末 open_pos=0、pending 空 |
| 全天 | ask_band **未改**；无 BIG_WIN / DAY_PROFIT；偶发 Gamma/Binance 报错与 `ask_above_max_price` 拒单，不改变结算口径 |

## 未来建议（仍服务保本金 → 验边）

1. **保持** $2/腿与 `ASK_MAX=0.40`；本日 WR−mean_fill 深度为负，**禁止**为冲 2%–5% 日涨幅或「回本」加仓
2. **正视** 手动解锁路径：首触熔断本已把日亏停在 −$8 附近，解锁后又亏一个熔断额度到 −$16。若保本金优先，熔断后默认锁到日末；解锁需显式、可审计，并接受「再亏 ≤$7（相对 baseline）」的硬顶
3. **保留** `DAY_LOSS_STOP=$7` 与 `CONSEC_LOSS_PAUSE=3`——二者都在干活；连亏暂停在解锁后的 21:13 也挡过半小时
4. 复盘继续 **只用 Gamma**；异常低 fill（如 0.02x）只当流动性噪声，不把「买得便宜」当 edge
5. GUIDED 维持冻结，直到 DAXUE 在更多日子里出现可持续的正 WR−mean_fill，而不是依赖单笔暴击

