# Polymarket BTC 5-minute Up/Down — Paper Backtest Report

**Mode:** PAPER ONLY — no live orders were placed.
**Profits are NOT guaranteed.** Past paper results do not predict future live results.

## Sample
- Markets collected: **200** closed `btc-updown-5m-*` windows
- With entry mid prices: **200**
- Date range (window start): **2026-10-19 17:40:00 Asia/Shanghai** → **2026-10-20 10:15:00 Asia/Shanghai**
- Outcomes in sample: Up wins=94, Down wins=106
- Max notional per trade: **$10** (matches user live limit)

## Methodology
1. Enumerate recent closed events via `gamma-api.polymarket.com/events?slug=btc-updown-5m-{unix}`.
2. Resolution from `outcomePrices` (Up/Down → 1/0).
3. Entry signal: mid from CLOB `/prices-history` (~15s after window open, fidelity=1).
4. Directional sizing: spend ≈ $10 notional (fee-adjusted) as **taker**.
5. Fees: crypto taker `fee = shares × 0.07 × p × (1−p)` (Polymarket docs; peaks ~3.5% of notional near p=0.50).
6. Winning shares pay $1 at resolution; losers $0.
7. **Arb:** buy BOTH Up and Down with equal shares when estimated ask_up+ask_down < 1−fee_buffer.
   - `entry_mid`: use entry mids as fill prices (optimistic).
   - `min_mid`: min aligned mid_up+mid_down during the 5m window (optimistic).
   - `min_ask_hs`: ask≈mid+0.005 (half-tick; still optimistic vs real book).
   - `min_ask_tick`: ask≈mid+0.01 (more conservative).
   - Fillability assumption: can take both displayed prices up to $10 total notional.
   - **No historical L2 book** was available for closed markets; arb edges are approximated.

## Directional strategies

| Strategy | Trades | Win rate | Gross PnL | Fees | Net PnL | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| naive_momentum | 143 | 70.6% | $164.33 | $36.20 | $128.13 | $34.04 |
| fade_favorite | 130 | 30.0% | $-236.98 | $55.35 | $-292.32 | $332.53 |
| always_up | 200 | 47.0% | $-136.83 | $69.31 | $-206.14 | $267.29 |
| always_down | 200 | 53.0% | $-5.54 | $65.58 | $-71.13 | $151.92 |

### Strategy definitions
- **naive_momentum**: if Up mid > 0.55 (and ≥ Down) buy Up; elif Down mid > 0.55 buy Down; else skip.
- **fade_favorite**: if one side mid < 0.45 and the other > 0.55, buy the cheap side.
- **always_up / always_down**: baseline — buy that side every market at entry mid.

## Up+Down mechanical arb

| Variant | #Opps | Opp rate | Avg edge gross | Avg edge net | Gross PnL | Net PnL | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| arb_entry_mid_buf0.01 | 27 | 13.5% | 5.52% | 2.28% | $16.28 | $7.01 | $0.35 |
| arb_entry_mid_buf0.02 | 20 | 10.0% | 6.80% | 3.56% | $14.95 | $7.99 | $0.07 |
| arb_min_ask_hs_buf0.01 | 56 | 28.0% | 5.12% | 1.81% | $31.03 | $11.43 | $0.56 |
| arb_min_ask_hs_buf0.02 | 44 | 22.0% | 5.99% | 2.69% | $28.63 | $13.17 | $0.29 |
| arb_min_ask_tick_buf0.01 | 42 | 21.0% | 5.18% | 1.86% | $23.49 | $8.79 | $0.69 |
| arb_min_ask_tick_buf0.02 | 34 | 17.0% | 5.99% | 2.67% | $22.07 | $10.06 | $0.13 |
| arb_min_mid_buf0.01 | 89 | 44.5% | 4.54% | 1.22% | $43.61 | $12.63 | $0.69 |
| arb_min_mid_buf0.02 | 56 | 28.0% | 6.12% | 2.81% | $37.34 | $17.56 | $0.09 |

## Recommendation

- Best directional **in this paper sample**: `naive_momentum` with net PnL **$128.13** over 143 trades (win rate 70.6%, max DD $34.04).
- Best arb variant **in this paper sample**: `arb_min_mid_buf0.02` net PnL **$17.56** over 56 opportunities.

### Live vs stay paper
- **naive_momentum** was the only directional strategy with positive paper PnL in this ~16.5h sample (+$128 net / 143 trades / 70.6% WR). Treat that as **hypothesis, not proof**: fills assumed at mid (real asks worse), one contiguous window, no out-of-sample. If you trial live at all, keep **hard $10 cap**, log fills vs mid, and stop if net expectancy turns negative after ~50 live trades.
- **fade_favorite / always_up / always_down**: clearly not worth live — large negative net PnL after crypto taker fees.
- **Up+Down arb**: mechanically sound *if* both asks + fees < $1, but this backtest **lacks true historical L2 asks**; mid/+tick approximations overstate opportunity frequency. A tiny live trial is only sensible with a live book gate (`ask_up + ask_down + fee_up + fee_down < 1`) and simultaneous dual-leg send; otherwise stay paper.
- **Default recommendation: stay paper.** Re-run on a second day / more markets before any scaled live. **Profits are NOT guaranteed.**

## Data gaps / assumptions
- No L2 order-book history for closed markets → arb uses mid/+tick approximations.
- Entry uses nearest 1-minute fidelity mid after open, not VWAP or actual fills.
- Assumed full fill at signal mid / approximated ask up to $10; ignored queue position & slippage.
- Maker rebates / taker rebate tiers ignored (assume worst-case pure taker, no rebate).
- Markets with missing price history skipped for directional signals.

## Files
- `report.md` (this file)
- `summary.json`
- `per_market.csv`
- `trades.csv`
