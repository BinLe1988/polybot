# polybot vs CloddsBot — BTC 5 分钟 Up/Down 纸面回测对比

**生成时间：** 2026-10-20 10:54:40 Asia/Shanghai
**模式：** 仅纸面（PAPER）— 未下任何真实订单。
**声明：** 纸面盈亏 ≠ 实盘盈亏；历史结果不保证未来。

## 一句话结论

**赢家（公平 ask 口径）：polybot 家族略胜 — `polybot_cs_edge0.001_ask_conservative` 净 PnL $8.42 vs CloddsBot 最优 `clodds_updown_arb_edge0.01` $8.37（差额仅 $0.05，实质接近平手）。方向性 CloddsBot（动量/均值回归）大幅亏损。勿把 `bid_approx`+$41 当实盘预期（假设 maker 零费率且能挂到 bid）。纸面≠实盘；5m≠polybot 原生 15m。**

## 样本
- 已关闭市场数：**200**（目标 200）
- 有价格序列：**200**
- 窗口起止（Asia/Shanghai）：**2026-10-19 18:05:00 Asia/Shanghai** → **2026-10-20 10:40:00 Asia/Shanghai**
- 结果分布：Up=95，Down=105
- 单笔名义上限：**$10**
- 手续费模型：taker `fee = shares × 0.07 × p × (1−p)`

## 方法说明
1. 同一批 `btc-updown-5m-*` 已关闭市场，所有策略共用。
2. 价格来自 CLOB `/prices-history`（fidelity=1），对齐 Up/Down mid。
3. **无历史 L2 盘口**；bid/ask 用 mid±0.005 近似。
4. 入场默认窗口开启后 ≥15 秒。

### polybot 家族（complete-set / Gabagool 逻辑）
- 条件：`1 - (bid_UP + bid_DOWN) ≥ min_edge` 时买入双边，持有至结算兑换 $1。
- `min_edge=0.001`：develop.yaml 默认；`min_edge=0.01`：research 回测默认。
- 门控标签：
  - `mid_optimistic`：用 mid 当成交价（偏乐观）
  - `bid_approx`：bid≈mid−0.005，假设 maker 挂单成交、手续费=0（乐观）
  - `ask_conservative`：ask≈mid+0.005，taker 双边买入（偏保守）
- 原生面向 15m/1h；本报告**改到 5m** 仅为公平对比。

### CloddsBot 家族
- **mean_reversion**：lookback=20，入场 z&lt;−2，出场 z&gt;0.5 / 止损10% / 止盈20%（源码默认）。
- **momentum**：short=5 / long=20，minMomentum=0.05，止损15% / 止盈30%。
- **updown_arb**：内部 Up+Down，当 ask_sum &lt; 1−0.01（minEdge≈1%，与 `/arbitrage` 默认一致）。
- 适配说明：单窗 5 分钟点数不足以填满 lookback=20，故 MR/动量在**跨连续市场的 mid 时间序列**上滚动计算；每市场最多一笔，未提前出场则持有至结算。

## 结果总表

| 策略 | 家族 | 笔数 | 市场数 | 胜率 | 毛 PnL | 手续费 | 净 PnL | 最大回撤 | 均边/信号 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `polybot_cs_edge0.001_bid_approx` | polybot | 197 | 197 | n/a(配对) | $41.45 | $0.00 | **$41.45** | $0.00 | 2.00% |
| `polybot_cs_edge0.01_bid_approx` | polybot | 197 | 197 | n/a(配对) | $41.45 | $0.00 | **$41.45** | $0.00 | 2.00% |
| `polybot_cs_edge0.001_ask_conservative` | polybot | 102 | 102 | n/a(配对) | $32.31 | $23.88 | **$8.42** | $0.82 | 2.96% |
| `polybot_cs_edge0.01_ask_conservative` | polybot | 81 | 81 | n/a(配对) | $31.38 | $23.01 | **$8.37** | $0.71 | 3.61% |
| `clodds_updown_arb_edge0.01` | cloddsbot | 81 | 81 | n/a(配对) | $31.38 | $23.01 | **$8.37** | $0.71 | 3.61% |
| `polybot_cs_edge0.01_mid_optimistic` | polybot | 122 | 122 | n/a(配对) | $41.57 | $34.97 | **$6.59** | $1.36 | 3.18% |
| `polybot_cs_edge0.001_mid_optimistic` | polybot | 124 | 124 | n/a(配对) | $41.63 | $35.24 | **$6.38** | $1.36 | 3.13% |
| `clodds_mean_reversion` | cloddsbot | 9 | 9 | 11.1% | $-59.10 | $6.82 | **$-65.92** | $65.92 | 22.76% |
| `clodds_momentum` | cloddsbot | 143 | 143 | 54.5% | $-42.49 | $62.96 | **$-105.45** | $134.17 | 34.74% |

## 跳过原因（摘要）

- `polybot_cs_edge0.001_mid_optimistic`: no_series=0, no_edge_in_window=76, entered=124
- `polybot_cs_edge0.001_bid_approx`: no_series=0, no_edge_in_window=3, entered=197
- `polybot_cs_edge0.001_ask_conservative`: no_series=0, no_edge_in_window=98, entered=102
- `polybot_cs_edge0.01_mid_optimistic`: no_series=0, no_edge_in_window=78, entered=122
- `polybot_cs_edge0.01_bid_approx`: no_series=0, no_edge_in_window=3, entered=197
- `polybot_cs_edge0.01_ask_conservative`: no_series=0, no_edge_in_window=119, entered=81
- `clodds_mean_reversion`: warmup=19, no_signal=0, entered=9, std_zero=0
- `clodds_momentum`: warmup=19, no_signal=0, entered=143
- `clodds_updown_arb_edge0.01`: no_series=0, no_edge=119, entered=81

## 家族对比
- polybot 家族账面最优（乐观 bid/maker）：`polybot_cs_edge0.001_bid_approx` 净 PnL **$41.45**（不可作实盘预期）
- polybot 公平 ask 最优：`polybot_cs_edge0.001_ask_conservative` 净 PnL **$8.42**
- CloddsBot 家族最优单策略：`clodds_updown_arb_edge0.01` 净 PnL **$8.37**
- CloddsBot 套件合计（MR+动量+UpDown套利 净 PnL 相加，非组合仓位）：**$-163.00**

## 重要局限 / Caveats
- 纸面成交 ≠ 实盘：无队列、无拒单、无延迟、无部分成交。
- 无历史 L2；mid±0.005 会高估可成交边。
- polybot 原生 15m/1h；5m 波动与盘口结构不同。
- CloddsBot MR/动量跨市场滚动是适配，非源码原样（源码默认 60s 轮询、更长寿命市场）。
- 配对套利净利在费用后常被吃掉；乐观 mid/bid 门控会虚高。
- 样本约 200 个连续窗口，非全历史；过拟合风险存在。

## 输出文件
- `compare/report.md`（本文件）
- `compare/summary.json`
- `compare/trades.csv`
- `compare/per_market.csv`
- `compare/cache/markets_*.json`（价格缓存，可复用）
