# BTC 5m DAXUE 昨日复盘日汇总 2026-10-28（Asia/Shanghai）

- 结算口径：**Polymarket Gamma**（`daxue_edge_snapshot.py` / `btc5m_poly_settle`）；不以 next-window-open 作主 W/L
- 源：`logs/reviews/daxue_edge_20261028.md`、`logs/daxue_day_state_20261028.json`、六份 `daxue_review_20261028_*.md`、`logs/daxue_live_20261028_*.log`
- GUIDED_LIVE：**冻结**（本日不挖 guided 复盘）
- 目标分层：保本金 → 验边 → 再谈日收益

## 保本金

- **日结算 PnL：+$19.73**（35 笔全部已结算；`day_state.day_pnl_settled=19.7316`）
- **日亏熔断（DAY_LOSS_STOP）**：未触发。日末 knobs 为 **$7**；相对熔断垫约 **+$26.73**。盘中 knobs 曾从约 $4 → $6 → $7 上调，但当日累计结算 PnL 始终 ≥ ~+$2.76，从未逼近熔断线
- **连亏暂停（CONSEC_LOSS_PAUSE=3 / PAUSE_MIN=30）**：日志未见实际 `CONSEC_LOSS_PAUSE` 跳过命中；但日内最长连亏达 **6**（约 11:16–13:22），属保本金观察项（午间回撤主因）
- **单笔大赚锁盈（BIG_WIN_LOCK=$6 / LOCK_GAINS）**：**已触发**。16:13 UP fill_px=0.15，单腿 **+$11.33**；此后 `lock_gains_stopped=true`，日末不再新开仓。21:42 前仍有一笔已挂仓 DOWN 结算后日 PnL 抬至 +$19.73
- **最大回撤（按 Gamma 结算顺序滚动）**：从早盘峰值约 **+$19.45**（09:45）回撤至谷底约 **+$2.76**（16:08），**峰谷回撤约 $16.69**；全日滚动累计 **未跌破 0**（日内没有「穿本」）
- 仓位尺度未变：$2/腿、`max_deploy=$13`、单窗一笔；相对 bankroll 记账值 $20，绝对盈利显著，但本层看重的是「未打穿、熔断未误伤、大赚后能锁住」

## 验边

- **成交笔数**：35（W=15 / L=20 / PENDING=0）
- **胜率 WR**：42.9%
- **均成交价 mean_fill**：0.2640（有 fill_px 的 12 笔均值同为 ~0.264；更早笔多用 decision ask 近似结算）
- **WR−mean_fill**：**+0.1646**
- **是否像市价随机**：不像。二元买价附近随机大致要求 WR≈fill 才打平；此处 WR 明显高于 mean_fill，**正 edge 信号偏强**（样本仍只是单日 35 笔，继续累计）
- 结构观察：午间 6 连亏后 edge 未崩（16:13 超大胜腿 + 晚间两笔 W 收复）；17:48 出现极端好价 fill_px≈0.036 仍判负（只亏本金 $2，未标 BAD_FILL）——便宜成交≠方向对
- 五笔复盘中早段仍有 next-open 估算法与 Gamma 不一致处（如部分 UP/DOWN 估胜实负）；**以本文件 / edge snapshot 的 Gamma 为准**

## 相对日收益目标

- 「每天 2%–5%」仅作对照愿望值，**不是当前 KPI**
- 旧软目标 **$0.80（DAILY_TARGET_SOFT）已停用**；晚间 Started 行已标 `DAILY_TARGET_SOFT=unused`，锁盈改由 **BIG_WIN_LOCK>$6** 驱动
- 对照说明：在 $2/腿、日约 35 笔尺度下，+$19.73 远高于「小幅日涨」叙事；不据此催促加仓或抬 `max_deploy`——优先确认 edge 可重复与回撤可控

## 当日风控与参数变化

| 时段（约） | 变化 / 事件 |
|---|---|
| 早盘～13:36 | `ask_band=[0.22,0.45]` / `ask_max=0.45`；风控 knobs 尚未完整打进 Started 横幅 |
| ~13:42 起 | **ASK_MAX 收紧 0.45→0.40**；买入改为 `decision_ask+BUY_SLIP(0.02)` maxPrice FOK；`bad_fill_over=0.03` |
| 午间 | 最长 6 连亏；滚动回撤最大（见上） |
| 16:13 | 单腿 +$11.33 → **BIG_WIN_LOCK / LOCK_GAINS** |
| 晚间 | DAY_LOSS_STOP knobs：$4 → $6 → **$7**；`DAILY_TARGET_SOFT` 停用 |
| 全天 | 多次 `MAX_RUN_SEC=3600` 干净退出后自动重启（约 33 个 live log）；若干 `ask_above_max_price` order-fail（非成交）；**无 BAD_FILL 实锤**；GUIDED 持续冻结；proxy 7897 / geoblock HK 正常 |

## 未来建议（仍服务保本金 → 验边）

1. **保持** $2/腿与 `ASK_MAX=0.40`，先用更多交易日验证 WR−mean_fill 是否稳定为正；不为冲 2%–5% 日涨幅加仓
2. **关注** 午间连亏簇：若多日再现 ≥3–4 连亏，再评估是否启用/收紧 `CONSEC_LOSS_PAUSE` 实战跳过（今日配置在、日志未见命中）
3. **保留** BIG_WIN_LOCK=$6：单日大赚后锁盈已兑现保本金优先级，避免回吐
4. 复盘与监控继续 **只用 Gamma**；旧五笔复盘里的 next-open 估法仅作历史对照
5. 极端低 fill（如 0.036）不视为新 alpha，也不据此放宽 ask 上限

