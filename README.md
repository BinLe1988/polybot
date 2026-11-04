# poly-live（基于 place_order.mjs）

## 干跑观察（每腿默认 ≤$2）
```bash
cd /Users/nick/poly-test
export HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 ALL_PROXY=http://127.0.0.1:7890
export NODE_OPTIONS=--use-env-proxy

# 单次扫盘（taker ask 边）
node strategy_btc5m_cs.mjs --amount 2 --min-edge 0.01

# maker 挂单计划（post-only @ bid）
node strategy_btc5m_cs.mjs --amount 2 --min-edge 0.01 --maker

# 连续干跑观察（约 10 分钟，15s 一轮）
node watch_btc5m_cs.mjs --amount 2 --min-edge 0.01 --interval 15 --rounds 40
```

实盘需 `--live` 且 `.env` 中 `POLYMARKET_LIVE=1`；单笔上限仍受 `POLYMARKET_MAX_ORDER_USD` 硬顶。
