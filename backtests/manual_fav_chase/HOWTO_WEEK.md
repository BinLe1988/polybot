# Favorite-chase 一周内回测怎么跑

时区：Asia/Shanghai。不要动 `.env`，不要重启 DAXUE/GUIDED。

## 路径 A（推荐）：真实成交带 / fill-tape

先 dump 钱包公开 activity，再按 R1/R2 过滤回放（与 `20261103_0000_0300` 同逻辑）。

```bash
cd /Users/nick/poly-test

# 近 7 个上海自然日（含今天）→ logs/manual_activity_week_….json
python3 backtests/dump_manual_activity.py --days 7 --session week_20261027_1103

# 或显式日期（含首尾日）
python3 backtests/dump_manual_activity.py \
  --start-sh 2026-10-27 --end-sh 2026-11-03 \
  --session week_20261027_1103

# 回放
python3 backtests/manual_fav_chase_backtest.py --session week_20261027_1103 --rule ALL
# 只要 R1 / R2：
python3 backtests/manual_fav_chase_backtest.py --session week_20261027_1103 --rule R1
python3 backtests/manual_fav_chase_backtest.py --session week_20261027_1103 --rule R2
```

输出：
- `backtests/manual_fav_chase/session_<id>_summary.json`
- `backtests/manual_fav_chase/session_<id>_trades.csv`

单段 3h 离线（已有 dump）：
```bash
python3 backtests/manual_fav_chase_backtest.py --session 20261103_0000_0300 --rule ALL
```

## 路径 B：公开 mid 模拟（`--fetch`）

不依赖自己成交；对每个 5m 窗口拉 Gamma settle + CLOB `prices-history`，用 mid±0.005 当 ask/bid，**首次**触发 R1/R2 入场。慢（约 5–8s/窗），默认 `--max-markets 96`（≈8h）。`0` = 不封顶。

```bash
# 近 N 天（脚本语义：从 today-N 到昨天，上海日）
python3 backtests/manual_fav_chase_backtest.py --fetch --days 7 --rule ALL --max-markets 0

# 显式区间（含首尾日）
python3 backtests/manual_fav_chase_backtest.py \
  --fetch --start-sh 2026-10-28 --end-sh 2026-11-02 \
  --rule ALL --max-markets 288

# 小样本冒烟
python3 backtests/manual_fav_chase_backtest.py \
  --fetch --start-sh 2026-11-02 --end-sh 2026-11-02 \
  --rule ALL --max-markets 24
```

输出：`fetch_summary.json` / `fetch_trades.csv`（及带日期后缀副本）。

## 数据现状（2026-11-03 检查）

| 数据 | 状态 |
|------|------|
| `logs/manual_activity_20261103_0000_0300.json` | 有（仅 00–03 段） |
| `logs/manual_activity_week_20261027_1103.json` | 已 dump（v2 activity，约 271 TRADE） |
| UV HB `undervalued_observe_*.log` | 仅约 11-02 起 |
| CLOB prices-history | 可在线拉，但单窗点很稀（常个位数） |
| 一周完整 L2 / 手续费字段 | 无 |

## 局限（务必读）

- 路径 A 的 PnL **未扣** maker/taker fee（activity 无 fee 字段）；未配对 BUY 记 `settle_stub` 不进 headline。
- dump 的是 funder `0xD18b…49B5` **全部**公开成交（含 bot），不是「纯手工 favorite-chase」标签。
- 路径 B 是 mid 代理，**不能**证明点击时点/盘口深度/滑点；稀疏 history 会偏乐观或漏触发。
- 小样本高方差；WR/PnL 不能当 live 预期。
