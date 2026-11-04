# BTC 5m 昨日复盘日汇总 2026-10-23（Asia/Shanghai）

- 覆盖：当日 8 份五笔复盘（#73–#112）+ 4 份配置变更记录；lifetime 日末 **112**（reviewed=112，pending=0）
- 日志：`logs/guided_live_20261023_*.log`（约 25 次 GUIDED_LIVE Started；整点 MAX_RUN=3600 重启 + 数次配置重启）
- 仓位全天 $2/笔，ask band 0.22–0.78，`REVIEW_PAUSE_EVERY=5`
- 日末现网：`LAG_MAX_ASK=0.60` `SR_RES_MAX=0.55` `SR_SUP_MAX=0.55` open tol **5/5**（SUP/RES）`SR_SUP_DROP=40/180s` trend-DOWN skip+60s CD `LAG_EDGE_LOG=0.06`（仅 LOG）
- 口径：盈亏为各批复盘/日志 `pnl_net` 记账，非链上逐笔核对；复盘时未定笔已尽量用后续 RESOLVE 补全并标注

## 一天做了哪些改动

| 时间 | 改动 | 来源 |
|---|---|---|
| ~05:55 | 新增 `SR_RESISTANCE_OPEN_TOL=5`：RESISTANCE DOWN 在 spot > open+$5 时 skip above-open | 用户采纳 03:08 建议（容差用 $5 非 $10）；`guided_review_20261023_0555.md`；PID 55940→59143，log `…_055555.log` |
| ~13:54 | 只放宽 `SR_RESISTANCE_MAX_ASK` **0.50→0.55**（干旱填单） | 用户指定只松一档；`guided_review_20261023_loosened_res_max.md`；PID 96097→99560，log `…_135434.log` |
| ~19:24 | 只收紧 `SR_SUPPORT_OPEN_TOL` **$10→$5** | #93–#97 后用户确认；`guided_review_20261023_sup_open_tol5.md`；PID 23975→26102，log `…_192458.log` |
| ~22:39 | 新增 `LAG_MAX_ASK=0.60`：lag UP/DOWN ask≥0.60 skip | #103–#107 后用户拍板；`guided_review_20261023_lag_max_ask60.md`；新 PID 41836，log `…_223912.log` |
| 早间已在线（跨自 10-22/10-23 初） | SUPPORT open tol 曾为 $10；drop-momentum 40/180s；RES 帽曾为 0.50；trend-DOWN skip+60s | 见 01:15/03:08 复盘 Started 行 |
| 全天未自动改 | lag 开盘侧硬过滤、lag left&lt;60s ban、RANGE early-exit、edge 0.06 硬化 | 各批复盘均 defer / 留给用户 |

## 效果如何（结合各批复盘与当日成交/跳过/错误）

**分批记账盈亏（约）**

| 批次报告 | 笔号 | 复盘时约 pnl | 补结算后约 | 备注 |
|---|---|---|---|---|
| 0115 | #73–77 | −$5.30（4 笔；#77 未定） | ≈−$7.38（#77 RESOLVE −$2.08） | #73 跨自 10-22 23:58；便宜逆向 lag 两连亏 |
| 0308 | #78–82 | ≈−$6.67（4 笔；#82 未定） | ≈−$3.99（#82 RESTORED 赢 +$2.68） | RES 开盘上方连亏 → 促成 above-open |
| 0640 | #83–87 | ≈−$3.42 | 同左 | open tol 内 SUPPORT 两连亏；无自动改 |
| 1124 | #88–92 | ≈+$0.37（4 笔；#92 未定） | ≈+$2.55（#92 +$2.18） | #90 便宜 lag +$4.80；above-open 已在挡 |
| 1915 | #93–97 | ≈−$7.41 | 同左 | 日间最差批；促成 SUP open tol→$5 |
| 2118 | #98–102 | ≈+$2.12 | 同左 | RES @0.47/0.53 两胜；松 RES_MAX 后首批像样 RES 样本 |
| 2233 | #103–107 | ≈+$4.82（4/5；#107 未定） | ≈+$2.77（#107 −$2.05） | #107 贵 ask 0.650 亏 → 促成 LAG_MAX_ASK |
| 2335 | #108–112 | ≈+$2.06 | 同左 | 新帽下 #109 @0.590 赢；#112 深折价 lag +$6.98 |

已知各批（尽量用补结算）粗加约 **−$12～−$13** 量级（#73–#112，含跨夜 #73）；午前连亏、傍晚三批转正。**非精确日结**，仅作方向参考。

**规则效果（结合当日日志跳过统计）**

- **ask-out-of-band**：约 **106** 次，仍是最大跳过来源（全局 band 未松）。
- **RESISTANCE ask 帽**：`ask-at-or-above-sr-res-max` 约 **31** 次。13:54 松到 0.55 后，晚间 #98/#99/#104/#105/#108 等陆续成交；21:18 两笔 RES 全胜，整体**不宜回调 0.50**（近 cap 仍有噪声亏，如 #104/#108）。
- **SUPPORT ask 帽**：`ask-at-or-above-sr-sup-max` 约 **25** 次，继续挡贵 SUPPORT。
- **RESISTANCE above-open**（05:55 上线）：约 **10** 次 skip；上午干旱期几乎无 RES 实盘，过滤在挡「开盘上方做空」。
- **SUPPORT below-open**：全日仅 **3** 次（tol=$10 两笔 + tol=$5 一笔 21:01）；19:24 收紧后行为符合预期。
- **drop-momentum**：约 **3** 次（如 drop=$68/$74/$123），样本少但方向对。
- **LAG_MAX_ASK=0.60**（22:39 上线）：上线后至日末日志**未见** `ask-at-or-above-lag-max` 命中；#109 @0.590&lt;0.60 放行且赢，帽行为正常。
- **trend-DOWN / SUPPORT 冷却**：当日 ENTRY skip 汇总里**未见**独立 trend-DOWN skip 行（冷却逻辑仍在 Started 配置中）；主要靠 ask/open/drop 过滤。
- **Cloudflare**：当日 live 日志 **0** 次。
- **执行噪声**：ERROR 约 31 行，多为 SELL `No resting liquidity` / balance·allowance、FAK BUY no match、偶发 Binance/连接回调；常见后果是 hold→resolve，非策略参数问题。当日 Oct23 日志内实盘 ENTRY（无 skip）约 **37** 笔。

**分段观感**：午前（#73–#92 一带）开盘错误侧 RES/SUPPORT 与便宜逆向 lag 拖累；13:54 松 RES 后干旱缓解靠 lag/SUPPORT，真正 RES 质量样本集中在 19:24 之后；晚间三批（#98–#112）合计记账约 **+$7** 量级，过滤组合（open tol 5/5 + RES/SUP 0.55 + lag 帽 0.60）方向好于午前。

## 各批复盘报告摘要

- `guided_review_20261023_0115.md`（#73–77，复盘 −$5.30 / 补后约 −$7.38）：便宜逆向 lag 两输；无自动改；提议 RES 对称开盘过滤与 SUP tol→$5（待定）。
- `guided_review_20261023_0308.md`（#78–82，复盘 ≈−$6.67 / 补后约 −$3.99）：RES 开盘上方连亏；#78 range_early 砍赢家；建议 RES above-open（用户后采纳 $5）。
- `guided_review_20261023_0555.md`（**配置变更，非五笔**）：上线 `SR_RES_OPEN_TOL=5`；其余规则不动。
- `guided_review_20261023_0640.md`（#83–87，≈−$3.42）：过滤有效；SUPPORT 在 tol=$10 内两连亏；不自动改；edge 0.06 仍只 LOG。
- `guided_review_20261023_1124.md`（#88–92，复盘 ≈+$0.37 / 补后约 +$2.55）：#90 便宜 lag 大赢；above-open 多次命中；无自动改。
- `guided_review_20261023_loosened_res_max.md`（**配置**，~13:54）：仅 `SR_RES_MAX` 0.50→0.55。
- `guided_review_20261023_1915.md`（#93–97，≈−$7.41）：日间最差；贵 ask lag 赔付差；边界 SUPPORT #97 满亏；无自动改，留给用户收紧 SUP tol / 讨论 lag 高 ask 帽。
- `guided_review_20261023_sup_open_tol5.md`（**配置**，~19:24）：`SR_SUP_OPEN_TOL` $10→$5。
- `guided_review_20261023_2118.md`（#98–102，≈+$2.12）：松 RES_MAX 后 RES 两胜；below-open tol=5 命中；无自动改。
- `guided_review_20261023_2233.md`（#103–107，复盘 ≈+$4.82 / 补后约 +$2.77）：已结算段强；#107 @0.650 待定后亏 −$2.05。
- `guided_review_20261023_lag_max_ask60.md`（**配置**，~22:39）：新增 `LAG_MAX_ASK=0.60`。
- `guided_review_20261023_2335.md`（#108–112，≈+$2.06）：新帽下无 ≥0.60 成交；#112 深折价大赢；无自动改；日末配置保持。

## 未来建议

1. **保持不回退**：`LAG_MAX_ASK=0.60`、`SR_RES/SUP_MAX=0.55`、open tol 5/5、drop-momentum 40/180s、trend-DOWN skip+60s、`LAG_EDGE_LOG=0.06` 仅 LOG——晚间证据支持继续观察，勿因单笔近 cap 亏损回调。
2. **仍 defer（跨批一致）**：lag「大幅偏离开盘仍入场」硬过滤；lag left&lt;60s（或 &lt;70s）ban；RANGE early-exit 收紧/关闭；edge 0.06 硬化为 skip。便宜 lag 仍有大赢样本（#90/#112），过早收紧易误杀。
3. **可选观察（不改码）**：RESISTANCE / SUPPORT 在 ask∈[0.50,0.55] 近 cap 带的单独期望；廉价 lag（ask≤0.25）在 open 错误侧做反方向的胜率。
4. **执行层**：SELL 无流动性与 FAK no match 仍频繁 → 继续依赖 resolve、不加仓；属基础设施噪声，非当日参数主因。
5. **复盘习惯**：继续结合**多批历史**再动刀，避免只看最新五笔撤销已验证规则（用户既有偏好，当日晚间三批也印证「午前亏 → 过滤收紧后转正」）。
