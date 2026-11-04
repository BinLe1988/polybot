# BTC 5m Human Strategy — Quantized Rules

Dry-run only unless `--live` **and** `POLYMARKET_LIVE=1`. Defaults assume bankroll **$20**, max leg **$2** (10%).

| # | Principle (用户原话意图) | Quantized threshold / action |
|---|--------------------------|------------------------------|
| 1 | ≤2分钟还不明朗 → 作对冲 | `remainingSec ≤ 120` 且 `gap < 0.15` → `hedge`（买弱侧或双边，默认对冲因子 x=1） |
| 2 | 3分钟前形成绝对优势 → 抓价差降单边成本 | `elapsedSec ≤ 180`（窗口前 3 分钟）且 favorite mid/ask ≥ **0.65** 或 gap ≥ **0.30** → `buy_fav_limit`（优先 maker/postOnly） |
| 3 | 2分钟内拉开≥40差距 → ~80成本加码 | `elapsedSec ≤ 120` 且 gap ≥ **0.40** → `add_at_80`（限价≈0.80，maker） |
| 4 | 1分钟以内不操作 | `remainingSec ≤ 60` → `skip` / `NO_TRADE` |
| 5 | 持仓方向明显不对 → x倍对冲减损 | 持仓侧 mid 相对对侧崩溃（己侧 mid < 0.40 且对侧 > 0.55）→ `cut_hedge`，size = x×持仓名义，默认 **x=1** |
| 6 | 一直在牌桌上 / 活下去 | 单腿 ≤ `$2`；总名义 ≤ 银行 30%；不追满仓赌结算 |
| 7 | 持续不明朗不交易 | 连续 ≥3 tick `gap < 0.12` → `unclear` → `skip` |
| 8 | 蜡烛均值回归 | 近 N tick 的 Up mid 路径若已大幅上行 → 轻微偏向 Down（反之亦然）；无 spot 时用盘口 mid 路径近似 |
| 9 | 当作 10% 一次交易 | 默认银行 `$20`，单笔目标 `$2`（对齐 `POLYMARKET_MAX_ORDER_USD`） |
| 10 | 大跌/大涨 → 满仓或 2/3 | 仅在 **oscillate** 且 favorite ≥ **0.75**：size = **2/3×max**；≥0.85 才 full。趋势盘见 #21 |
| 11 | 不确定挂 50 | regime=`unclear` 且未触发 skip 硬规则、且 `remaining > 120` → `quote_50`（postOnly @0.50） |
| 12 | 一直2分钟内且20%以内需≥80%胜率 | `remaining ≤ 120` 且 gap ≤ **0.20** → 除非 favorite ≥ **0.80** 否则 `skip` |
| 13 | 不冒险投入 >30% 仓位 | `positionNotional ≤ 0.30 × bankroll`（$6）；单腿 ≤ `$2` |
| 14 | 1-Tap / 低费率优先 | dry-run 标记 `postOnly: true`；优先 maker 挂单而非吃单 |
| 15 | 盘中多次应对震荡 | `oscillate` 允许初始入场 + **最多 1 次**加码（见 #19） |
| 16 | 波动不大减少订单量 | 近窗 mid 振幅 range < **0.08** → size × **0.5** |
| 17 | 纪律 | 严格执行上表硬规则，不覆盖 late/min-size/max-add |
| 18 | 不做 20% 以下仓位；早止盈 | sizeUsd < **$0.40** → skip；持仓侧 mid ≥ **0.90** → `take_profit` |
| 19 | 不要连续加单 | 初始后最多 **1** 次 add；连续 add 无 flip 确认则禁止 |
| 20 | 足够资金 | dry-run 状态机跟踪 `bankroll` / 持仓名义 |
| 21 | 大趋势不操作，只做波动盘 | regime=`trend`（单向 mid 漂移）→ `skip`；只在 `oscillate` 主动开仓 |

## Defaults (documented, not asked)

- Bankroll: `$20`
- Hedge factor x: `1`
- Unclear gap: `< 0.12` (soft), `< 0.15` for early hedge
- Favorite clear: mid ≥ `0.65` or gap ≥ `0.30`
- Big gap add: ≥ `0.40` within first 2 min elapsed
- Late: remaining ≤ `60s`
- Min leg: `$0.40`; max leg: `$2`; max total position: `$6`
- Regime from Up/Down mid history (no external BTC spot required)
- Live: requires both `--live` and `POLYMARKET_LIVE=1`; human strategy **never places** live orders in v1 (dry-run propose only)

## Polymarket fees (crypto / updown dry-run)

Polymarket taker fee on shares (crypto markets):

\`\`\`
fee_shares = shares × 0.07 × p × (1 − p)
\`\`\`

With \`shares = notionalUsd / p\`:

\`\`\`
feeUsd = notionalUsd × 0.07 × (1 − p)
\`\`\`

| Role | Actions | feeModel | feeUsd |
|------|---------|----------|--------|
| **Maker / postOnly (1-Tap)** | \`buy_fav_limit\`, \`add_at_80\`, \`quote_50\`, \`take_profit\` (limit sell at bid) | \`maker0\` | **0** |
| **Taker** | \`cut_hedge\` (buy opposite at ask), \`hedge\` (postOnly:false) | \`taker07\` | \`usd × 0.07 × (1−p)\` |

Dry-run JSON / JSONL fields when a trade is proposed:

- \`feeUsd\`, \`feeModel\` (\`maker0\` \| \`taker07\`)
- \`netCostUsd\` = usd + fee for buys (proceeds negative net for sells)
- \`expectedEdgeAfterFee\` ≈ shares×mid − netCost (buys)

Ledger files:

- \`logs/btc5m_human_state.json\` — full strategy state
- \`logs/btc5m_human_ledger.json\` — bankroll, cash, position, realizedPnl, feesPaid, equity (mtm), fills[], settlements[]

**Window settlement proxy:** on slug/window change, open position settles with **last mid of position side ≥ 0.5 → win ($1/share), else $0**. Gamma resolution is not fetched; document this as a dry-run proxy. Fees already paid remain in \`feesPaid\`; settlement PnL is payout − notional cost.
