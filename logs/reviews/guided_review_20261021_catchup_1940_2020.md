# BTC 5m 复盘（历史补追 · **不足5笔**）— 2026-10-21 19:40–20:20（Asia/Shanghai）

- **类型**：catch-up historical（归档补追，**不足5笔补追**）
- **漏触发原因**：同前——进程计数随 `MAX_RUN` 重置；且本段仅 **2** 笔，本就不会单独触发 5 笔门闩
- **本批**：16:27 之后全局序列 **#11–#12**（其后 #13–#17 已写入 `guided_review_20261021_2215.md`，本稿不重复）
- **会话日志**：
  - `guided_live_20261021_185342.log`（#11）
  - `guided_live_20261021_201350.log`（#12）
- **仓位**：amt=$2.00；`SR_RES_MAX=0.65`；band `[0.22,0.78]`
- **暂停**：归档 → **不暂停实盘**；**不**下调 `lifetime_entries` / `reviewed_lifetime_entries`
- **姊妹报告**：
  - `guided_review_20261021_catchup_1652_1750.md`（#1–5）
  - `guided_review_20261021_catchup_1822_1931.md`（#6–10）
  - `guided_review_20261021_1627.md` / `guided_review_20261021_2215.md`

## 两笔明细（不足5笔）

1. **19:40:41** SUPPORT **UP** @**0.530** slug=`btc-updown-5m-1792582800` amt=$2.00  
   - **早退成功**：`[GUIDED][LIVE][EXIT] range_early UP bid=0.520 pnl_net=$-0.169` @19:42:11。约 **−$0.17**。  
   - 会话 `185342` FINAL：entries=4 early_exits=1 pnl_net=**$+6.45**（含 #8–#10 盈利 + 本笔早退）。

2. **20:19:59** SUPPORT **UP** @**0.630** slug=`btc-updown-5m-1792585200` amt=$2.00（日志 `201350`）  
   - 20:23:26 **SELL 失败**：`not enough balance / allowance`（balance 3076922 < order 3170000）→ **hold to resolve**。  
   - 结算：guided `0 → +1.12`（20:28 STATS，Resolved 1）。约 **+$1.12**。  
   - 随后同日志进入 #13（20:25 SUPPORT UP @0.680）等，已在 2215 报告覆盖。

**本批 LIVE 粗算合计**：−$0.17 +$1.12 ≈ **+$0.95**。

## 观察

- **不足5笔**：仅作时间线补洞；不单独作为调参样本权重。
- 早退一成功（薄亏）一失败（allowance 微短后退持有反赢）——再次说明 **执行层 SELL** 与策略方向脱钩。
- #12 赢后会话继续，与 2215 中 #13 的 `+1.12→+2.02` 衔接一致。

## 自动改动

无。

## 待定 / 下次（仅建议，未落地）

- 与批 B 相同：修复 SELL size / allowance 取整，避免「差几百 share units 无法出货」。
- 不因本 2 笔调整 SR / band。

## 交叉链接

完整 16:27 后 #1–#17 现由：本三份 catch-up（#1–12）+ `guided_review_20261021_2215.md`（#13–17）覆盖。
