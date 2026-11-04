# BTC 5m DAXUE 昨日复盘日汇总 2026-10-31（Asia/Shanghai）

- 结算口径：**Polymarket Gamma**（`daxue_day_state` / `btc5m_poly_settle`）；不以 next-window-open 作主 W/L
- 源：`logs/daxue_day_state_20261031.json`（**权威日末**）、`logs/reviews/daxue_edge_20261031.md`（2026-11-01 00:03 刷新，n=12 与 day_state 一致）、两份 `daxue_review_20261031_*.md`（过程切片；01:47 含跨日前笔，**勿单独当全日**）、约 25 个 `logs/daxue_live_20261031_*.log`
- GUIDED_LIVE：**冻结**（本日不挖 guided 复盘）
- 目标分层：保本金 → 验边 → 再谈日收益

## 保本金

- **日结算 PnL：−$7.13**（12 笔全部已结算；`day_state.day_pnl_settled=-7.1286`，**3W/9L**，`pending={}`）
- **日亏熔断（DAY_LOSS_STOP=$7）**：**触发一次**，日末仍锁死
  - **23:16:31** 入场 DOWN@0.31 后立刻按 provisional 计：`day_pnl_for_stop=−$7.13<=−$7.00`（当时 settled 仍 −$5.13 + 开仓 −$2）→ `day_loss_stopped=true`，停止新 ENTRY；开仓持有至结算
  - **23:24:49** Gamma 结算 L −$2 → settled=**−$7.13**；相对熔断线穿透约 **$0.13**（cushion=−$0.13）；`manual_resume=false`
- **连亏暂停（CONSEC_LOSS_PAUSE=3 / PAUSE_MIN=30）**：**2 次 WARNING**
  - **17:00:22** streak=3 → 暂停至 17:30:22（settled=−$5.13；午后 15:05/15:39/16:53 三连 L）
  - **23:24:49** streak=4 → 暂停至 23:54:49（settled=−$7.13；已叠日亏熔断，以熔断为主）
- **单笔大赚锁盈（BIG_WIN_LOCK=$6 / LOCK_GAINS）**：**未触发**（`max_leg=$+4.06` < $6；`lock_gains_stopped=false`）
- **DAY_PROFIT_LOCK=$15**：**未触发**（`day_profit_stopped=false`）
- **最大回撤相关**：滚动 settled 盘中峰值约 **+$1.47**（12:28 第 5 笔 W 后），其后一路下行至日末 **−$7.13**；自峰值回撤约 **$8.59**；相对 0 的谷底即日末 −$7.13。日末 `open_pos=0`、`consec_losses=4`
- 仓位尺度：默认 **$2/腿**；约 **21:10–23:01** 临时降至 **$1**（CLOB 余额），**23:01:56** 起恢复 **$2**；`ask_band=[0.22,0.40]` 全日未改；`max_deploy=$13`；相对 bankroll 记账 $20，本层结论是**熔断在 −$7 附近挡住了继续加仓，日亏约等于一个熔断额度**

### 十二笔 Gamma 结算一览（fill 时刻 / 方向 / fill_px）

| # | 约 fill 时刻 | 方向 | ask | fill_px | PnL | slug |
|---|---|---|---|---|---|---|
| 1 | 01:29 | DOWN | 0.40 | **0.12** | −2.00 | 1793381100 |
| 2 | 01:40 | DOWN | 0.39 | 0.37 | **+3.41** | 1793382000 |
| 3 | 02:54 | DOWN | 0.39 | 0.30 | −2.00 | 1793386500 |
| 4 | 11:07 | UP | 0.39 | 0.30 | −2.00 | 1793415900 |
| 5 | 12:23 | DOWN | 0.40 | 0.33 | **+4.06** | 1793420400 |
| 6 | 12:29 | DOWN | 0.39 | 0.29 | −2.00 | 1793420700 |
| 7 | 13:52 | UP | 0.24 | 0.22 | −2.00 | 1793425800 |
| 8 | 13:56 | UP | 0.38 | 0.37 | **+3.41** | 1793426100 |
| 9 | 15:05 | DOWN | 0.39 | 0.25 | −2.00 | 1793430300 |
| 10 | 15:39 | UP | 0.29 | 0.23 | −2.00 | 1793432100 |
| 11 | 16:53 | DOWN | 0.38 | 0.31 | −2.00 | 1793436600 |
| 12 | 23:16 | DOWN | 0.36 | 0.31 | −2.00 | 1793459700 |

## 验边

- **成交笔数**：12（W=3 / L=9 / PENDING=0）——`day_state` 与 edge snapshot 一致
- **胜率 WR**：**25.0%**（3/12）
- **均成交价 mean_fill**：**0.2833**（含 01:29 异常低价 0.12；剔除该笔后 mean≈**0.2982**）
- **WR−mean_fill**：**−0.0333**（剔除 0.12 后约 **−0.048**）
- **是否像市价随机**：**略差于打平、偏负 edge**。二元买价附近随机大致要求 WR≈fill 才打平；本日 WR=0.25 低于 mean_fill≈0.28，差距约 3pp，**不像明显正 edge**，也未到昨日 0W8L 那种深度负样本。三笔赢腿 fill 偏贵（0.37/0.33/0.37），九笔亏腿里多笔「买得便宜仍 L」（0.12/0.22/0.23/0.25）
- 结构观察：凌晨 3 笔后长时间横盘在 −$0.59～+$1.47；午后密集成交后自 +$0.87 滑至 −$5.13 并触发连亏暂停；晚间多数时间无成交，末笔 23:16 再亏触熔断。五笔复盘为过程切片，**以本文件 / day_state 的 Gamma 为准**
- 日志无独立 `[BAD_FILL]` WARNING（`bad_fill_over=0.03` 指相对 ask 买贵）；01:29 fill=0.12 相对 ask=0.40 为**异常低 fill**，仍计 L −$2

## 相对日收益目标

- 「每天 2%–5%」仅作对照愿望值，**不是当前 KPI**
- 旧软目标 **$0.80（DAILY_TARGET_SOFT）已停用**；Started 行标 `DAILY_TARGET_SOFT=unused`；锁盈由 **BIG_WIN_LOCK>$6** 与 **DAY_PROFIT_LOCK=$15** 驱动（本日均未命中）
- 对照说明：在 $2/腿、日 12 笔尺度下，−$7.13 ≈ 记账 bankroll $20 的约 −36%，且刚好卡在日亏熔断线附近；**不据此催促加仓或抬 `max_deploy`**——优先保本金与把 WR−mean_fill 拉回非负，而不是用仓位回补

## 当日风控与参数变化

| 时段（约） | 变化 / 事件 |
|---|---|
| 全日 Started | `ask_band=[0.22,0.40]` / `ask_max=0.40`；`DAY_LOSS_STOP=$7`；`CONSEC_LOSS_PAUSE=3` / 30m；`BIG_WIN_LOCK=$6`；`DAY_PROFIT_LOCK=$15`；`DAILY_TARGET_SOFT=unused`；GUIDED 冻结；proxy 7897 |
| 00:13–~21:05 | 多次 `MAX_RUN_SEC=3600` 干净重启（约 20 次级）；POSITION_USD=$2；尚无 overnight/adverse-slip 标签 |
| 01:29–02:54 | 凌晨 3 笔：L/W/L → settled −$0.59；01:29 异常低 fill 0.12 |
| 03:00–11:00 | 长时间无成交（多次 `ask_above_max_price` 拒单）；settled 停在 −$0.59 |
| 11:07–16:53 | 午后 8 笔密集：至 14:04 曾回正至 +$0.87，随后 15:05–16:53 三连 L → −$5.13 |
| 17:00 | **CONSEC_LOSS_PAUSE** 至 17:30；之后至晚间仍 unlocked、无新成交 |
| ~21:05 | 启用 **overnight_pause 00–08**、**adverse_slip≥0.10**、**stale_cheap**（ask≥0.35&fill≤0.25） |
| ~21:10 | **POSITION_USD=$1**（临时；余额）；22:07 一例 `adverse-slip` 拒单；22:51 `ask_above_max_price` |
| ~23:01 | **POSITION_USD 恢复 $2** |
| 23:16–23:24 | 第 12 笔 FILL → **DAY_LOSS_STOP**；结算 L → settled −$7.13；再触 **CONSEC** streak=4 至 23:54 |
| 全天 | ask_band **未改**；无 BIG_WIN / DAY_PROFIT / 手动解锁；偶发 `ask_above_max_price` 与 1 次 adverse-slip，不改变结算口径 |

## 未来建议（仍服务保本金 → 验边）

1. **保持** $2/腿与 `ASK_MAX=0.40`；本日 WR−mean_fill 小幅为负，**禁止**为冲 2%–5% 日涨幅或「回本」加仓
2. **保留** `DAY_LOSS_STOP=$7`——本日在 provisional −$7.13 即停新开，避免更深穿透；勿在锁仓后手动解锁除非显式接受「再亏一个熔断额度」
3. **保留** `CONSEC_LOSS_PAUSE=3`——17:00 的 30 分钟暂停挡住了午后连亏后的继续开火；与熔断叠用时以熔断为主
4. 复盘继续 **只用 Gamma**；异常低 fill（如 0.12）只当流动性噪声，不把「买得便宜」当 edge；overnight / adverse-slip 已于晚间启用，观察其对次日凌晨样本的过滤效果
5. GUIDED 维持冻结，直到 DAXUE 在更多日子里出现可持续的非负 WR−mean_fill，而不是依赖单笔接近 BIG_WIN 的暴击（本日 max_leg +$4.06 仍未达锁盈）

