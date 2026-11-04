#!/usr/bin/env node
/**
 * BTC 5m Up+Down complete-set explorer (place_order SecureClient path).
 * Default: dry-run. Live only with --live AND POLYMARKET_LIVE=1.
 * On Mac behind Clash: NODE_OPTIONS=--use-env-proxy and HTTPS_PROXY=http://127.0.0.1:7890
 */
import { createSecureClient, OrderSide } from "@polymarket/client";
import { privateKey } from "@polymarket/client/viem";
import { loadEnv, parseArgs } from "./lib_env.mjs";

const FEE_RATE = 0.07;
const DEFAULT_MIN_EDGE = 0.01;

function takerFee(shares, price) {
  const p = Math.max(0, Math.min(1, Number(price)));
  return shares * FEE_RATE * p * (1 - p);
}

function windowStarts(base = Math.floor(Date.now() / 1000)) {
  const cur = Math.floor(base / 300) * 300;
  // prefer current then next, then previous (for late fills)
  return [cur, cur + 300, cur + 600, cur - 300];
}

async function fetchJson(url) {
  const res = await fetch(url, {
    headers: { Accept: "application/json", "User-Agent": "poly-live-cs/1.1" },
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
  const askSize =
    ask == null ? 0 : Number((asks.find((a) => Number(a.price) === ask) || {}).size || 0);
  return { ask, bid, askSize };
}

function assetIdForOutcome(market, names) {
  const entries = Object.entries(market.outcomes || {});
  for (const want of names) {
    for (const [k, v] of entries) {
      if (String(k).toLowerCase() === want.toLowerCase()) {
        return market.version === "v2" ? v.positionId : v.tokenId;
      }
    }
  }
  // some SDK shapes: outcomes.yes / .no with .name
  for (const [, v] of entries) {
    const n = String(v?.name || "").toLowerCase();
    if (names.some((w) => n === w.toLowerCase())) {
      return market.version === "v2" ? v.positionId : v.tokenId;
    }
  }
  return null;
}

async function main() {
  const args = parseArgs();
  const env = loadEnv();
  const minEdge = Number(args["min-edge"] || DEFAULT_MIN_EDGE);
  const wantLive = args.live === "true" || args.live === "1";
  const doLive = wantLive && env.live;
  const amountArg = args.amount != null ? Number(args.amount) : Math.min(env.maxOrderUsd, 2);
  const amountCap = Math.min(env.maxOrderUsd, amountArg);
  const makerMode = args.maker === "true" || args.maker === "1";
  const nowMs = Date.now();

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
      if (upBook.ask == null || downBook.ask == null) {
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
    console.log(JSON.stringify({ ok: false, error: "no_tradeable_btc5m", scanned }));
    process.exit(2);
  }

  const upBook = picked._books.up;
  const downBook = picked._books.down;
  const askSum = upBook.ask + downBook.ask;
  const bidSum =
    upBook.bid != null && downBook.bid != null ? upBook.bid + downBook.bid : null;
  const edgeAsk = 1 - askSum;
  const edgeBid = bidSum != null ? 1 - bidSum : null;

  const maxByUp = amountCap / upBook.ask;
  const maxByDown = amountCap / downBook.ask;
  let shares =
    Math.floor(Math.min(maxByUp, maxByDown, upBook.askSize || 1e9, downBook.askSize || 1e9) * 100) /
    100;
  if (!(shares > 0)) shares = Math.floor(Math.min(maxByUp, maxByDown) * 100) / 100;
  const cost = shares * askSum;
  const fees = takerFee(shares, upBook.ask) + takerFee(shares, downBook.ask);
  const expectedNet = shares * 1 - cost - fees;

  // Maker plan: post-only buys at best bid when bidSum edge >= minEdge (fee≈0 optimistic)
  let makerShares = 0;
  let makerCost = null;
  let makerNet = null;
  let makerEdgeOk = false;
  if (bidSum != null && upBook.bid > 0 && downBook.bid > 0) {
    const mEdge = 1 - bidSum;
    makerEdgeOk = mEdge >= minEdge;
    const maxU = amountCap / upBook.bid;
    const maxD = amountCap / downBook.bid;
    makerShares = Math.floor(Math.min(maxU, maxD) * 100) / 100;
    makerCost = makerShares * bidSum;
    makerNet = makerShares * 1 - makerCost; // assume maker fee 0
  }

  const plan = {
    ok: true,
    mode: doLive ? (makerMode ? "LIVE_MAKER" : "LIVE") : (makerMode ? "DRY_RUN_MAKER" : "DRY_RUN"),
    liveEnv: env.live,
    makerMode,
    slug: picked.slug,
    question: picked.question,
    endDate: picked.endDate || picked.end_date_iso,
    upAsk: upBook.ask,
    downAsk: downBook.ask,
    upBid: upBook.bid,
    downBid: downBook.bid,
    askSum,
    bidSum,
    edgeAsk,
    edgeBid,
    minEdge,
    edgeOk: edgeAsk >= minEdge,
    feeAwareOk: expectedNet > 0,
    shares,
    cost: Number(cost.toFixed(4)),
    fees: Number(fees.toFixed(4)),
    expectedNet: Number(expectedNet.toFixed(4)),
    makerEdgeOk,
    makerShares,
    makerCost: makerCost != null ? Number(makerCost.toFixed(4)) : null,
    makerExpectedNet: makerNet != null ? Number(makerNet.toFixed(4)) : null,
    amountCapPerLeg: amountCap,
  };

  if (makerMode) {
    if (!plan.makerEdgeOk || !(makerShares > 0)) {
      console.log(
        JSON.stringify(
          { ...plan, action: "skip", reason: !plan.makerEdgeOk ? "no_maker_edge" : "size_zero", scanned },
          null,
          2
        )
      );
      return;
    }
    if (!doLive) {
      console.log(
        JSON.stringify(
          {
            ...plan,
            action: "would_post_both_bids",
            postOnly: true,
            upLimit: upBook.bid,
            downLimit: downBook.bid,
          },
          null,
          2
        )
      );
      return;
    }
    // LIVE maker: place post-only limits at bid
    const client = await createSecureClient({
      wallet: env.wallet,
      signer: privateKey(env.privateKey),
    });
    const market = await client.fetchMarket({ slug: plan.slug });
    const upAsset = assetIdForOutcome(market, ["Up", "yes"]);
    const downAsset = assetIdForOutcome(market, ["Down", "no"]);
    if (!upAsset || !downAsset) {
      console.log(JSON.stringify({ ...plan, action: "abort", reason: "missing_asset_ids" }));
      process.exit(1);
    }
    const notionalUp = makerShares * upBook.bid;
    const notionalDown = makerShares * downBook.bid;
    if (notionalUp > env.maxOrderUsd || notionalDown > env.maxOrderUsd) {
      console.log(JSON.stringify({ ...plan, action: "abort", reason: "amount_cap" }));
      process.exit(1);
    }
    const upResp = await client.placeLimitOrder({
      assetId: upAsset,
      side: OrderSide.BUY,
      price: upBook.bid,
      size: makerShares,
      postOnly: true,
    });
    const downResp = await client.placeLimitOrder({
      assetId: downAsset,
      side: OrderSide.BUY,
      price: downBook.bid,
      size: makerShares,
      postOnly: true,
    });
    console.log(JSON.stringify({ ...plan, action: "posted_limits", upOrder: upResp, downOrder: downResp }));
    if (!upResp.ok || !downResp.ok) process.exit(1);
    return;
  }

  if (!plan.edgeOk || !(shares > 0)) {
    console.log(
      JSON.stringify(
        { ...plan, action: "skip", reason: !plan.edgeOk ? "no_edge" : "size_zero", scanned },
        null,
        2
      )
    );
    return;
  }

  if (!doLive) {
    console.log(JSON.stringify({ ...plan, action: "would_buy_both_legs" }));
    return;
  }

  const client = await createSecureClient({
    wallet: env.wallet,
    signer: privateKey(env.privateKey),
  });
  const market = await client.fetchMarket({ slug: plan.slug });
  const upAsset = assetIdForOutcome(market, ["Up", "yes"]);
  const downAsset = assetIdForOutcome(market, ["Down", "no"]);
  if (!upAsset || !downAsset) {
    console.log(
      JSON.stringify(
        {
          ...plan,
          action: "abort",
          reason: "missing_asset_ids",
          outcomeKeys: Object.keys(market.outcomes || {}),
        },
        null,
        2
      )
    );
    process.exit(1);
  }
  const amtUp = Math.min(amountCap, shares * upBook.ask);
  const amtDown = Math.min(amountCap, shares * downBook.ask);
  if (!(amtUp > 0) || !(amtDown > 0) || amtUp > env.maxOrderUsd || amtDown > env.maxOrderUsd) {
    console.log(JSON.stringify({ ...plan, action: "abort", reason: "amount_cap", amtUp, amtDown }));
    process.exit(1);
  }

  const upResp = await client.placeMarketOrder({
    assetId: upAsset,
    side: OrderSide.BUY,
    amount: String(Number(amtUp.toFixed(2))),
  });
  const downResp = await client.placeMarketOrder({
    assetId: downAsset,
    side: OrderSide.BUY,
    amount: String(Number(amtDown.toFixed(2))),
  });
  console.log(
    JSON.stringify(
      {
        ...plan,
        action: "placed",
        upOrder: upResp,
        downOrder: downResp,
      },
      null,
      2
    )
  );
  if (!upResp.ok || !downResp.ok) process.exit(1);
}

main().catch((e) => {
  console.error(JSON.stringify({ ok: false, error: String(e?.message || e) }));
  process.exit(1);
});
