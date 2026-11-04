#!/usr/bin/env node
/**
 * BTC 5m Human principles strategy (dry-run by default).
 * 将用户实盘原则量化为可执行规则 — 详见 RULES.md / 文件顶注释。
 *
 * Live only if --live AND POLYMARKET_LIVE=1；本版 dry-run 仅输出意图，不真正下单。
 * Proxy: NODE_OPTIONS=--use-env-proxy HTTPS_PROXY=http://127.0.0.1:7890
 *
 * Fee model (Polymarket crypto/updown style):
 *  - Taker: feeUsd = notionalUsd * 0.07 * (1 - p)  [= shares * 0.07 * p * (1-p)]
 *  - Maker / postOnly (1-Tap): feeUsd = 0
 *  Maker actions: buy_fav_limit, add_at_80, quote_50, take_profit (limit sell)
 *  Taker actions: cut_hedge, hedge (buy opposite at ask)
 *
 * 原则 → 量化（摘要）:
 *  1  ≤2min 不明朗 → hedge
 *  2  前3min 绝对优势 → buy_fav_limit 降成本
 *  3  前2min gap≥40¢ → add_at_80
 *  4  remaining≤60s → skip
 *  5  方向错 → cut_hedge x=1
 *  6/13/20  仓位帽 $2/腿、$6/总、bankroll$20
 *  7  持续 gap<0.12 → skip
 *  8  mid路径均值回归偏置
 *  9  单笔≈10%=$2
 * 10  oscillate+fav≥0.75 → 2/3仓
 * 11  unclear → quote_50 maker
 * 12  ≤2min且gap≤0.20 需 fav≥0.80 否则 skip
 * 14  优先 postOnly/maker
 * 15/19  最多1次加码，无连续加
 * 16  低波动 size×0.5
 * 18  min$0.40；mid≥0.90 take_profit
 * 21  trend 单向 → skip，只做 oscillate
 */
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { loadEnv, parseArgs } from "./lib_env.mjs";

const DIR = dirname(fileURLToPath(import.meta.url));
const STATE_PATH = join(DIR, "logs", "btc5m_human_state.json");
const LEDGER_PATH = join(DIR, "logs", "btc5m_human_ledger.json");

// —— 量化常量 ——
const BANKROLL0 = 20;
const MAX_LEG = 2;
const MAX_POS_FRAC = 0.3; // 30% bankroll
const MIN_LEG = 0.4; // 20% of $2
const LATE_SEC = 60;
const HEDGE_REMAIN_SEC = 120;
const EARLY_ADV_ELAPSED = 180; // 前3分钟
const ADD80_ELAPSED = 120; // 前2分钟
const FAV_CLEAR = 0.65;
const GAP_CLEAR = 0.3;
const GAP_ADD40 = 0.4;
const GAP_UNCLEAR = 0.12;
const GAP_SOFT = 0.15;
const GAP_LATE_EDGE = 0.2;
const FAV_HIGH_CONF = 0.8;
const FAV_BIG = 0.75;
const TAKE_PROFIT_MID = 0.9;
const WRONG_SIDE_MID = 0.4;
const OPP_STRONG = 0.55;
const LOW_RANGE = 0.08;
const HEDGE_X = 1;
const UNCLEAR_STREAK = 3;
const TREND_STREAK = 4;
const TREND_MOVE = 0.08;
const WINDOW_SEC = 300;
const TAKER_FEE_RATE = 0.07; // Polymarket crypto/updown taker fee coefficient

/** Maker actions: postOnly / 1-Tap — fee 0. Taker: cut_hedge, hedge. take_profit = limit sell → maker0. */
const MAKER_ACTIONS = new Set(["buy_fav_limit", "add_at_80", "quote_50", "take_profit"]);
const TAKER_ACTIONS = new Set(["cut_hedge", "hedge"]);

/**
 * Polymarket fee on shares: fee = shares × 0.07 × p × (1−p)
 * With shares = usd/p → feeUsd = usd × 0.07 × (1−p). Maker → 0.
 */
function feeUsdFor(action, sizeUsd, price) {
  const p = Number(price);
  const usd = Number(sizeUsd) || 0;
  if (!(usd > 0) || !(p > 0) || !(p < 1)) {
    return { feeUsd: 0, feeModel: "maker0", netCostUsd: usd, shares: 0 };
  }
  const shares = usd / p;
  if (MAKER_ACTIONS.has(action) || (!TAKER_ACTIONS.has(action) && action !== "cut_hedge" && action !== "hedge")) {
    // default unknown trade-like → treat as maker0 if postOnly style; skip/noop already size 0
    if (TAKER_ACTIONS.has(action)) {
      /* fall through below */
    } else {
      return {
        feeUsd: 0,
        feeModel: "maker0",
        netCostUsd: usd,
        shares: Number(shares.toFixed(6)),
      };
    }
  }
  if (TAKER_ACTIONS.has(action)) {
    const feeUsd = Number((usd * TAKER_FEE_RATE * (1 - p)).toFixed(6));
    return {
      feeUsd,
      feeModel: "taker07",
      netCostUsd: Number((usd + feeUsd).toFixed(6)),
      shares: Number(shares.toFixed(6)),
    };
  }
  return {
    feeUsd: 0,
    feeModel: "maker0",
    netCostUsd: usd,
    shares: Number(shares.toFixed(6)),
  };
}

function expectedEdgeAfterFee(action, sizeUsd, price, midFair, feeUsd) {
  const p = Number(price);
  const usd = Number(sizeUsd) || 0;
  if (!(usd > 0) || !(p > 0)) return null;
  const shares = usd / p;
  const fair = midFair != null ? Number(midFair) : p;
  if (action === "take_profit") {
    // selling shares at p; fair mark is mid; fee usually 0
    const proceeds = usd; // sizeUsd is notional sold at limit
    const mark = shares * fair;
    return Number((proceeds - mark - (feeUsd || 0)).toFixed(6));
  }
  // buy: cost netCost, fair value shares*mid
  const net = usd + (feeUsd || 0);
  return Number((shares * fair - net).toFixed(6));
}

function windowStarts(base = Math.floor(Date.now() / 1000)) {
  const cur = Math.floor(base / 300) * 300;
  return [cur, cur + 300, cur + 600, cur - 300];
}

async function fetchJson(url) {
  const res = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "poly-human/1.0" },
  });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.json();
}

async function gammaBySlug(slug) {
  const data = await fetchJson(
    `https://gamma-api.polymarket.com/markets?slug=${encodeURIComponent(slug)}`
  );
  return Array.isArray(data) ? data[0] || null : data;
}

function parseTokens(m) {
  let tokens = m.clobTokenIds;
  if (typeof tokens === "string") tokens = JSON.parse(tokens);
  let outs = m.outcomes;
  if (typeof outs === "string") outs = JSON.parse(outs);
  if (!tokens || tokens.length < 2) return null;
  const upI = outs?.indexOf?.("Up") ?? 0;
  const downI = outs?.indexOf?.("Down") ?? 1;
  return { up: String(tokens[upI]), down: String(tokens[downI]), outcomes: outs };
}

async function bestBook(tokenId) {
  const book = await fetchJson(`https://clob.polymarket.com/book?token_id=${tokenId}`);
  const asks = book.asks || [];
  const bids = book.bids || [];
  const ask = asks.length ? Math.min(...asks.map((a) => Number(a.price))) : null;
  const bid = bids.length ? Math.max(...bids.map((b) => Number(b.price))) : null;
  return { ask, bid };
}

function mid(bid, ask) {
  if (bid != null && ask != null) return (bid + ask) / 2;
  if (ask != null) return ask;
  if (bid != null) return bid;
  return null;
}

function loadState() {
  try {
    if (!existsSync(STATE_PATH)) return null;
    return JSON.parse(readFileSync(STATE_PATH, "utf8"));
  } catch {
    return null;
  }
}

function saveState(st) {
  mkdirSync(join(DIR, "logs"), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(st, null, 2));
  // Mirror ledger snapshot (same object; dedicated path for accounting readers)
  const ledger = {
    bankroll: st.bankroll,
    cash: st.cash,
    position: st.position,
    realizedPnl: st.realizedPnl,
    feesPaid: st.feesPaid,
    equity: st.equity,
    fills: st.fills || [],
    settlements: st.settlements || [],
    slug: st.slug,
    tick: st.tick,
    feeFormula: "taker: usd*0.07*(1-p); maker/postOnly: 0",
    settlementProxy: "on window change: last mid of position side >= 0.5 → win ($1/share), else $0; no gamma resolution fetch",
    updatedAt: new Date().toISOString(),
  };
  writeFileSync(LEDGER_PATH, JSON.stringify(ledger, null, 2));
}

function defaultState(slug, bankroll) {
  return {
    slug,
    bankroll,
    cash: bankroll,
    realizedPnl: 0,
    feesPaid: 0,
    equity: bankroll,
    fills: [],
    settlements: [],
    upMidHist: [],
    downMidHist: [],
    unclearStreak: 0,
    position: null, // { side, notional, avgPrice, shares, adds, openedAt, feesOnPos }
    lastAction: null,
    lastAddAt: null,
    tick: 0,
    lastUpMid: null,
    lastDownMid: null,
  };
}

/** Settle open position when window/slug changes. Proxy: last mid of side >= 0.5 → win. */
function settleWindow(st, reason = "window_change") {
  if (!st || !st.position) return st;
  const pos = st.position;
  const side = pos.side;
  const lastMid = side === "Up" ? st.lastUpMid : st.lastDownMid;
  const shares =
    pos.shares != null
      ? Number(pos.shares)
      : Number(pos.notional || 0) / Math.max(1e-9, Number(pos.avgPrice || 0.5));
  const won = lastMid != null ? lastMid >= 0.5 : false;
  const payout = Number((shares * (won ? 1 : 0)).toFixed(6));
  const cost = Number(pos.notional || 0);
  const pnl = Number((payout - cost).toFixed(6)); // fees already deducted from cash / feesPaid
  st.cash = Number((Number(st.cash || 0) + payout).toFixed(6));
  st.realizedPnl = Number((Number(st.realizedPnl || 0) + pnl).toFixed(6));
  st.settlements = st.settlements || [];
  st.settlements.push({
    at: new Date().toISOString(),
    slug: st.slug,
    side,
    shares: Number(shares.toFixed(6)),
    avgPrice: pos.avgPrice,
    notional: cost,
    lastMid,
    won,
    payout,
    pnl,
    reason,
    proxy: "last_mid_ge_0.5",
  });
  // keep fills list bounded
  if (st.settlements.length > 200) st.settlements = st.settlements.slice(-200);
  st.position = null;
  return st;
}

function mtmEquity(st, upMid, downMid) {
  let eq = Number(st.cash || 0);
  if (st.position) {
    const side = st.position.side;
    const m = side === "Up" ? upMid : downMid;
    const shares =
      st.position.shares != null
        ? Number(st.position.shares)
        : Number(st.position.notional || 0) / Math.max(1e-9, Number(st.position.avgPrice || 0.5));
    if (m != null) eq += shares * m;
    else eq += Number(st.position.notional || 0);
  }
  return Number(eq.toFixed(6));
}

/** 从 mid 历史推断 regime */
function inferRegime(upHist, downHist, gap, remainingSec) {
  if (remainingSec <= LATE_SEC) return "late";
  if (gap < GAP_UNCLEAR) return "unclear";

  const n = upHist.length;
  if (n < 3) return gap < GAP_SOFT ? "unclear" : "oscillate";

  const recent = upHist.slice(-6);
  let upMoves = 0;
  let downMoves = 0;
  let flips = 0;
  for (let i = 1; i < recent.length; i++) {
    const d = recent[i] - recent[i - 1];
    if (d > 0.01) upMoves++;
    else if (d < -0.01) downMoves++;
    if (i >= 2) {
      const d0 = recent[i - 1] - recent[i - 2];
      if (d0 * d < 0 && Math.abs(d0) > 0.005 && Math.abs(d) > 0.005) flips++;
    }
  }
  const net = recent[recent.length - 1] - recent[0];
  const range = Math.max(...recent) - Math.min(...recent);

  if (Math.abs(net) >= TREND_MOVE && Math.min(upMoves, downMoves) <= 1 && flips <= 1) {
    return "trend";
  }
  if (flips >= 1 || (range >= 0.05 && upMoves >= 1 && downMoves >= 1)) {
    return "oscillate";
  }
  if (gap < GAP_SOFT) return "unclear";
  return Math.abs(net) >= TREND_MOVE ? "trend" : "oscillate";
}

function meanReversionBias(upHist) {
  if (upHist.length < 3) return 0;
  const net = upHist[upHist.length - 1] - upHist[0];
  if (net > 0.12) return -1;
  if (net < -0.12) return 1;
  if (net > 0.06) return -0.5;
  if (net < -0.06) return 0.5;
  return 0;
}

function clampSize(usd, bankroll, posNotional, maxLeg) {
  const maxPos = bankroll * MAX_POS_FRAC;
  const room = Math.max(0, maxPos - posNotional);
  let s = Math.min(usd, maxLeg, room);
  s = Math.floor(s * 100) / 100;
  if (s > 0 && s < MIN_LEG) return 0;
  return s;
}

function emit(obj) {
  console.log(JSON.stringify(obj));
}

async function main() {
  const args = parseArgs();
  const env = loadEnv();
  const wantLive = args.live === "true" || args.live === "1";
  const doLive = false;
  const liveFlag = wantLive && env.live;
  const amountCap = Math.min(
    env.maxOrderUsd,
    Number(args.amount != null ? args.amount : Math.min(env.maxOrderUsd, MAX_LEG))
  );
  const bankrollArg = args.bankroll != null ? Number(args.bankroll) : BANKROLL0;
  const nowMs = Date.now();
  const nowSec = Math.floor(nowMs / 1000);

  const slugs = args.slug
    ? [args.slug]
    : windowStarts().map((t) => `btc-updown-5m-${t}`);

  let picked = null;
  let tokens = null;
  const scanned = [];
  for (const slug of slugs) {
    try {
      const m = await gammaBySlug(slug);
      if (!m) {
        scanned.push({ slug, status: "miss" });
        continue;
      }
      const end = Date.parse(m.endDate || m.end_date_iso || "") || 0;
      const closed = m.closed === true || (end && end <= nowMs);
      const t = parseTokens(m);
      scanned.push({ slug, closed, end: m.endDate || m.end_date_iso, hasTokens: !!t });
      if (closed || !t) continue;
      const [upBook, downBook] = await Promise.all([bestBook(t.up), bestBook(t.down)]);
      if (upBook.ask == null && downBook.ask == null) {
        scanned[scanned.length - 1].thinBook = true;
        continue;
      }
      picked = m;
      tokens = t;
      picked._books = { up: upBook, down: downBook };
      break;
    } catch (e) {
      scanned.push({ slug, error: String(e.message || e) });
    }
  }

  if (!picked) {
    emit({
      ok: false,
      live: false,
      action: "skip",
      reason: "no_tradeable_btc5m",
      scanned,
      feeUsd: 0,
      feeModel: "maker0",
      netCostUsd: 0,
    });
    process.exit(2);
  }

  const upBook = picked._books.up;
  const downBook = picked._books.down;
  const upMid = mid(upBook.bid, upBook.ask);
  const downMid = mid(downBook.bid, downBook.ask);
  const endMs = Date.parse(picked.endDate || picked.end_date_iso || "") || 0;
  const remainingSec = endMs ? Math.max(0, Math.floor((endMs - nowMs) / 1000)) : WINDOW_SEC;
  let windowStart = null;
  const mSlug = String(picked.slug || "").match(/btc-updown-5m-(\d+)/);
  if (mSlug) windowStart = Number(mSlug[1]);
  else if (endMs) windowStart = Math.floor(endMs / 1000) - WINDOW_SEC;
  const elapsedSec = windowStart != null ? Math.max(0, nowSec - windowStart) : WINDOW_SEC - remainingSec;

  const gap =
    upMid != null && downMid != null ? Math.abs(upMid - downMid) : 0;
  const favSide =
    upMid != null && downMid != null
      ? upMid >= downMid
        ? "Up"
        : "Down"
      : upBook.ask != null && downBook.ask != null
        ? upBook.ask <= downBook.ask
          ? "Up"
          : "Down"
        : "Up";
  const favMid = favSide === "Up" ? upMid : downMid;
  const weakSide = favSide === "Up" ? "Down" : "Up";
  const weakMid = weakSide === "Up" ? upMid : downMid;
  const favAsk = favSide === "Up" ? upBook.ask : downBook.ask;
  const weakAsk = weakSide === "Up" ? upBook.ask : downBook.ask;
  const favBid = favSide === "Up" ? upBook.bid : downBook.bid;

  // —— 状态 / ledger ——
  let st = loadState();
  if (!st) {
    st = defaultState(picked.slug, bankrollArg);
  } else if (st.slug !== picked.slug) {
    // settle prior window with last mid proxy before resetting window fields
    settleWindow(st, "window_change");
    const carry = {
      bankroll: st.bankroll != null ? st.bankroll : bankrollArg,
      cash: st.cash != null ? st.cash : bankrollArg,
      realizedPnl: st.realizedPnl || 0,
      feesPaid: st.feesPaid || 0,
      fills: st.fills || [],
      settlements: st.settlements || [],
    };
    st = defaultState(picked.slug, carry.bankroll);
    st.cash = carry.cash;
    st.realizedPnl = carry.realizedPnl;
    st.feesPaid = carry.feesPaid;
    st.fills = carry.fills;
    st.settlements = carry.settlements;
    st.equity = mtmEquity(st, upMid, downMid);
  }
  // migrate old state missing ledger fields
  if (st.cash == null) st.cash = st.bankroll != null ? st.bankroll : bankrollArg;
  if (st.bankroll == null) st.bankroll = bankrollArg;
  if (st.realizedPnl == null) st.realizedPnl = 0;
  if (st.feesPaid == null) st.feesPaid = 0;
  if (!st.fills) st.fills = [];
  if (!st.settlements) st.settlements = [];

  st.tick = (st.tick || 0) + 1;
  st.upMidHist = [...(st.upMidHist || []), upMid].filter((x) => x != null).slice(-20);
  st.downMidHist = [...(st.downMidHist || []), downMid].filter((x) => x != null).slice(-20);
  st.lastUpMid = upMid;
  st.lastDownMid = downMid;
  if (gap < GAP_UNCLEAR) st.unclearStreak = (st.unclearStreak || 0) + 1;
  else st.unclearStreak = 0;

  const regime = inferRegime(st.upMidHist, st.downMidHist, gap, remainingSec);
  const mrBias = meanReversionBias(st.upMidHist);
  const recentUps = st.upMidHist.slice(-6);
  const midRange =
    recentUps.length >= 2 ? Math.max(...recentUps) - Math.min(...recentUps) : 0.15;
  const lowVol = midRange < LOW_RANGE;

  const pos = st.position;
  const posNotional = pos ? Number(pos.notional || 0) : 0;
  const posSide = pos?.side || null;
  const adds = pos?.adds || 0;

  st.equity = mtmEquity(st, upMid, downMid);

  const base = {
    ok: true,
    live: false,
    liveWanted: !!liveFlag,
    mode: "DRY_RUN_HUMAN",
    window: picked.slug,
    question: picked.question,
    endDate: picked.endDate || picked.end_date_iso,
    remainingSec,
    elapsedSec,
    up: { mid: upMid, ask: upBook.ask, bid: upBook.bid },
    down: { mid: downMid, ask: downBook.ask, bid: downBook.bid },
    gap: Number(gap.toFixed(4)),
    favSide,
    favMid,
    regime,
    bankroll: st.bankroll,
    cash: st.cash,
    realizedPnl: st.realizedPnl,
    feesPaid: st.feesPaid,
    equity: st.equity,
    position: pos,
    postOnly: true,
    amountCapPerLeg: amountCap,
    tick: st.tick,
    feeFormula: "taker: usd*0.07*(1-p); maker0: 0",
  };

  function decide(action, extra = {}) {
    const sizeUsd = extra.sizeUsd ?? 0;
    const price = extra.price ?? null;
    const side = extra.side ?? null;
    const feeInfo = sizeUsd > 0 && price != null
      ? feeUsdFor(action, sizeUsd, price)
      : { feeUsd: 0, feeModel: "maker0", netCostUsd: sizeUsd || 0, shares: 0 };

    const midFair =
      side === "Up" ? upMid : side === "Down" ? downMid : favMid;
    const edge =
      sizeUsd > 0 && price != null
        ? expectedEdgeAfterFee(action, sizeUsd, price, midFair, feeInfo.feeUsd)
        : null;

    const out = {
      ...base,
      action,
      side,
      price,
      sizeUsd,
      reason: extra.reason ?? action,
      hedgeX: extra.hedgeX ?? null,
      mrBias,
      lowVol,
      feeUsd: feeInfo.feeUsd,
      feeModel: feeInfo.feeModel,
      netCostUsd: feeInfo.netCostUsd,
      expectedEdgeAfterFee: edge,
      shares: feeInfo.shares || null,
      ...extra.more,
    };

    // —— dry-run ledger updates ——
    if (["buy_fav_limit", "add_at_80", "quote_50", "hedge", "cut_hedge"].includes(action) && sizeUsd > 0) {
      const px = price || 0.5;
      const usd = sizeUsd;
      const shares = feeInfo.shares || usd / px;
      const fee = feeInfo.feeUsd || 0;
      const net = feeInfo.netCostUsd;

      // pay cash for buys (including taker fee)
      st.cash = Number((Number(st.cash) - net).toFixed(6));
      st.feesPaid = Number((Number(st.feesPaid) + fee).toFixed(6));

      const fill = {
        at: new Date().toISOString(),
        action,
        side,
        price: px,
        sizeUsd: usd,
        shares: Number(shares.toFixed(6)),
        feeUsd: fee,
        feeModel: feeInfo.feeModel,
        netCostUsd: net,
        expectedEdgeAfterFee: edge,
        window: picked.slug,
      };
      st.fills.push(fill);
      if (st.fills.length > 500) st.fills = st.fills.slice(-500);

      if (action === "cut_hedge" || (action === "hedge" && posSide && side !== posSide)) {
        // buy opposite: reduce original notional/shares; hedge cost already in cash
        if (st.position) {
          const red = Math.min(Number(st.position.notional), usd);
          const shRed =
            st.position.shares != null
              ? (red / Math.max(1e-9, Number(st.position.avgPrice))) 
              : shares;
          st.position.notional = Math.max(0, Number(st.position.notional) - red);
          if (st.position.shares != null) {
            st.position.shares = Math.max(0, Number(st.position.shares) - shRed);
          }
          st.position.feesOnPos = Number(((st.position.feesOnPos || 0) + fee).toFixed(6));
          if (st.position.notional < MIN_LEG) st.position = null;
        }
      } else if (action === "quote_50" || action === "buy_fav_limit" || action === "add_at_80" || action === "hedge") {
        if (!st.position) {
          st.position = {
            side,
            notional: usd,
            avgPrice: px,
            shares: Number(shares.toFixed(6)),
            adds: 0,
            openedAt: nowSec,
            feesOnPos: fee,
          };
        } else if (st.position.side === side) {
          const n0 = Number(st.position.notional);
          const a0 = Number(st.position.avgPrice);
          const s0 = Number(st.position.shares || n0 / Math.max(1e-9, a0));
          st.position.avgPrice = (a0 * n0 + px * usd) / (n0 + usd);
          st.position.notional = n0 + usd;
          st.position.shares = Number((s0 + shares).toFixed(6));
          st.position.feesOnPos = Number(((st.position.feesOnPos || 0) + fee).toFixed(6));
          if (action === "add_at_80") {
            st.position.adds = (st.position.adds || 0) + 1;
            st.lastAddAt = nowSec;
          }
        } else {
          st.position.notional = Math.max(0, Number(st.position.notional) - usd);
          if (st.position.shares != null) {
            st.position.shares = Math.max(0, Number(st.position.shares) - shares);
          }
          if (st.position.notional < MIN_LEG) {
            st.position = {
              side,
              notional: usd,
              avgPrice: px,
              shares: Number(shares.toFixed(6)),
              adds: 0,
              openedAt: nowSec,
              feesOnPos: fee,
            };
          }
        }
      }
    }

    if (action === "take_profit" && st.position) {
      const px = price != null ? price : (posSide === "Up" ? upBook.bid : downBook.bid) || st.position.avgPrice;
      const sellUsd = sizeUsd || st.position.notional;
      const sh =
        st.position.shares != null
          ? Math.min(Number(st.position.shares), sellUsd / Math.max(1e-9, px || 0.5))
          : sellUsd / Math.max(1e-9, Number(st.position.avgPrice || 0.5));
      const feeInfoTp = feeUsdFor("take_profit", sellUsd, px || 0.5);
      const proceeds = Number((sh * (px || 0.5) - feeInfoTp.feeUsd).toFixed(6));
      const costPart =
        st.position.shares != null && st.position.shares > 0
          ? (sh / st.position.shares) * Number(st.position.notional)
          : sellUsd;
      const pnl = Number((proceeds - costPart).toFixed(6));
      st.cash = Number((Number(st.cash) + proceeds).toFixed(6));
      st.feesPaid = Number((Number(st.feesPaid) + feeInfoTp.feeUsd).toFixed(6));
      st.realizedPnl = Number((Number(st.realizedPnl) + pnl).toFixed(6));
      st.fills.push({
        at: new Date().toISOString(),
        action: "take_profit",
        side: posSide,
        price: px,
        sizeUsd: sellUsd,
        shares: Number(sh.toFixed(6)),
        feeUsd: feeInfoTp.feeUsd,
        feeModel: feeInfoTp.feeModel,
        netCostUsd: Number((-proceeds).toFixed(6)),
        expectedEdgeAfterFee: edge,
        window: picked.slug,
        pnl,
      });
      out.feeUsd = feeInfoTp.feeUsd;
      out.feeModel = feeInfoTp.feeModel;
      out.netCostUsd = Number((-proceeds).toFixed(6));
      out.sizeUsd = sellUsd;
      out.price = px;
      st.position = null;
    }

    st.equity = mtmEquity(st, upMid, downMid);
    out.cash = st.cash;
    out.realizedPnl = st.realizedPnl;
    out.feesPaid = st.feesPaid;
    out.equity = st.equity;
    out.position = st.position;

    st.lastAction = action;
    saveState(st);
    emit(out);
  }

  // ========== 决策树（硬规则优先）==========

  if (remainingSec <= LATE_SEC) {
    return decide("skip", { reason: "late_no_trade_le60s" });
  }

  if (pos && posSide) {
    const pMid = posSide === "Up" ? upMid : downMid;
    if (pMid != null && pMid >= TAKE_PROFIT_MID) {
      return decide("take_profit", {
        side: posSide,
        price: posSide === "Up" ? upBook.bid : downBook.bid,
        sizeUsd: Math.min(posNotional, amountCap),
        reason: "take_profit_mid_ge_0.90",
      });
    }
  }

  if (pos && posSide) {
    const pMid = posSide === "Up" ? upMid : downMid;
    const oMid = posSide === "Up" ? downMid : upMid;
    if (pMid != null && oMid != null && pMid < WRONG_SIDE_MID && oMid > OPP_STRONG) {
      const hedgeUsd = clampSize(HEDGE_X * posNotional, st.bankroll, 0, amountCap);
      if (hedgeUsd >= MIN_LEG) {
        const opp = posSide === "Up" ? "Down" : "Up";
        return decide("cut_hedge", {
          side: opp,
          price: opp === "Up" ? upBook.ask : downBook.ask,
          sizeUsd: hedgeUsd,
          hedgeX: HEDGE_X,
          reason: "position_wrong_cut_hedge_x1",
          more: { postOnly: false },
        });
      }
    }
  }

  if (regime === "trend") {
    return decide("skip", { reason: "trend_regime_skip_only_oscillate" });
  }

  if (st.unclearStreak >= UNCLEAR_STREAK && gap < GAP_UNCLEAR) {
    if (remainingSec > HEDGE_REMAIN_SEC && regime === "unclear") {
      const sz = clampSize(lowVol ? amountCap * 0.5 : amountCap * 0.5, st.bankroll, posNotional, amountCap);
      if (sz >= MIN_LEG && !pos) {
        const side = mrBias > 0 ? "Up" : mrBias < 0 ? "Down" : favSide;
        return decide("quote_50", {
          side,
          price: 0.5,
          sizeUsd: sz,
          reason: "unclear_quote_50_maker",
        });
      }
    }
    return decide("skip", { reason: "sustained_unclear_gap" });
  }

  if (remainingSec <= HEDGE_REMAIN_SEC && gap <= GAP_LATE_EDGE) {
    if (!(favMid >= FAV_HIGH_CONF)) {
      if (pos && gap < GAP_SOFT) {
        const sz = clampSize(Math.min(amountCap, posNotional * HEDGE_X), st.bankroll, 0, amountCap);
        if (sz >= MIN_LEG) {
          return decide("hedge", {
            side: weakSide,
            price: weakAsk,
            sizeUsd: sz,
            hedgeX: HEDGE_X,
            reason: "le2min_unclear_hedge",
            more: { postOnly: false },
          });
        }
      }
      return decide("skip", { reason: "le2min_gap_le20_need_80pct" });
    }
  }

  if (remainingSec <= HEDGE_REMAIN_SEC && gap < GAP_SOFT) {
    if (pos) {
      const sz = clampSize(Math.min(amountCap, posNotional || amountCap * 0.5), st.bankroll, 0, amountCap);
      if (sz >= MIN_LEG) {
        return decide("hedge", {
          side: weakSide,
          price: weakAsk,
          sizeUsd: sz,
          reason: "le2min_unclear_hedge_soft",
          more: { postOnly: false },
        });
      }
    }
    return decide("skip", { reason: "le2min_unclear_no_pos_skip" });
  }

  if (
    elapsedSec <= ADD80_ELAPSED &&
    gap >= GAP_ADD40 &&
    favMid != null &&
    regime === "oscillate"
  ) {
    if (pos && pos.side === favSide && adds >= 1) {
      return decide("skip", { reason: "no_consecutive_add_already_added" });
    }
    if (pos && pos.side === favSide && st.lastAction === "add_at_80") {
      return decide("skip", { reason: "no_consecutive_add" });
    }
    let sz = amountCap;
    if (favMid >= FAV_BIG) sz = amountCap * (favMid >= 0.85 ? 1 : 2 / 3);
    if (lowVol) sz *= 0.5;
    if ((favSide === "Up" && mrBias < -0.5) || (favSide === "Down" && mrBias > 0.5)) {
      sz *= 0.5;
    }
    sz = clampSize(sz, st.bankroll, posNotional, amountCap);
    if (sz >= MIN_LEG) {
      const isAdd = !!(pos && pos.side === favSide);
      return decide(isAdd ? "add_at_80" : "buy_fav_limit", {
        side: favSide,
        price: 0.8,
        sizeUsd: sz,
        reason: isAdd
          ? "early_gap40_add_at_80"
          : "early_gap40_open_target_80",
      });
    }
  }

  if (
    elapsedSec <= EARLY_ADV_ELAPSED &&
    regime === "oscillate" &&
    ((favMid != null && favMid >= FAV_CLEAR) || gap >= GAP_CLEAR)
  ) {
    if (pos && pos.side === favSide && adds >= 1) {
      return decide("skip", { reason: "have_position_no_more_add" });
    }
    if (pos && pos.side === favSide && st.lastAction === "add_at_80") {
      return decide("skip", { reason: "no_consecutive_add" });
    }
    let sz = amountCap * 0.75;
    if (favMid >= FAV_BIG) sz = amountCap * (2 / 3);
    if (lowVol) sz *= 0.5;
    if ((favSide === "Up" && mrBias < -0.5) || (favSide === "Down" && mrBias > 0.5)) {
      sz *= 0.5;
    }
    sz = clampSize(sz, st.bankroll, posNotional, amountCap);
    const limitPx =
      favBid != null
        ? favBid
        : favAsk != null
          ? Math.max(0.01, Math.round((favAsk - 0.02) * 100) / 100)
          : 0.5;
    if (sz >= MIN_LEG) {
      const isAdd = !!(pos && pos.side === favSide);
      if (isAdd) {
        return decide("skip", { reason: "already_in_fav_wait_gap40_or_tp" });
      }
      return decide("buy_fav_limit", {
        side: favSide,
        price: limitPx,
        sizeUsd: sz,
        reason: "early_clear_fav_maker_lower_cost",
      });
    }
  }

  if (regime === "unclear" && remainingSec > HEDGE_REMAIN_SEC && !pos) {
    const sz = clampSize(amountCap * (lowVol ? 0.4 : 0.5), st.bankroll, 0, amountCap);
    if (sz >= MIN_LEG) {
      const side = mrBias > 0 ? "Up" : mrBias < 0 ? "Down" : favSide;
      return decide("quote_50", {
        side,
        price: 0.5,
        sizeUsd: sz,
        reason: "uncertain_quote_50",
      });
    }
  }

  if (
    regime === "oscillate" &&
    remainingSec > HEDGE_REMAIN_SEC &&
    favMid != null &&
    favMid >= 0.58 &&
    gap >= 0.16 &&
    !pos
  ) {
    let sz = amountCap * 0.5;
    if (lowVol) sz *= 0.5;
    sz = clampSize(sz, st.bankroll, 0, amountCap);
    const limitPx =
      favBid != null
        ? favBid
        : favAsk != null
          ? Math.max(0.01, Math.round((favAsk - 0.01) * 100) / 100)
          : 0.55;
    if (sz >= MIN_LEG) {
      return decide("buy_fav_limit", {
        side: favSide,
        price: limitPx,
        sizeUsd: sz,
        reason: "oscillate_mild_fav_probe",
      });
    }
  }

  return decide("skip", {
    reason:
      regime === "unclear"
        ? "unclear_no_setup"
        : lowVol
          ? "low_vol_no_edge"
          : "no_rule_matched",
  });
}

main().catch((e) => {
  emit({
    ok: false,
    live: false,
    action: "skip",
    reason: "error",
    error: String(e?.message || e),
    feeUsd: 0,
    feeModel: "maker0",
    netCostUsd: 0,
  });
  process.exit(1);
});
