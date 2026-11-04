#!/usr/bin/env node
/**
 * Watcher for strategy_btc5m_human.mjs — dry-run loop, jsonl under logs/.
 * 默认不传 --live；与 CS watcher 同样走代理环境。
 * Passes through feeUsd / feeModel / netCostUsd / expectedEdgeAfterFee / ledger fields.
 */
import { execFileSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { appendFileSync, mkdirSync, readFileSync, existsSync } from "fs";

const dir = dirname(fileURLToPath(import.meta.url));
const script = join(dir, "strategy_btc5m_human.mjs");
const args = {};
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  if (argv[i].startsWith("--")) {
    const k = argv[i].slice(2);
    const v = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
    args[k] = v;
  }
}

const amount = args.amount || "2";
const intervalSec = Number(args.interval || 15);
const rounds = Number(args.rounds || 40);
const live = args.live === "true" || args.live === "1";
const bankroll = args.bankroll || "20";
const logDir = join(dir, "logs");
mkdirSync(logDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[:.]/g, "-");
const logFile = join(logDir, `watch_btc5m_human_${stamp}.jsonl`);
const statePath = join(logDir, "btc5m_human_state.json");
const ledgerPath = join(logDir, "btc5m_human_ledger.json");

const childEnv = {
  ...process.env,
  HTTPS_PROXY: process.env.HTTPS_PROXY || "http://127.0.0.1:7890",
  HTTP_PROXY: process.env.HTTP_PROXY || "http://127.0.0.1:7890",
  ALL_PROXY: process.env.ALL_PROXY || "http://127.0.0.1:7890",
  NODE_OPTIONS: "--use-env-proxy",
};

function ts() {
  return new Date().toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
}

function parseOut(out) {
  const text = String(out || "").trim();
  if (!text) return { parseError: true, out: "" };
  const lines = text.split(/\n/).map((l) => l.trim()).filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      return JSON.parse(lines[i]);
    } catch {}
  }
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(text.slice(start, end + 1));
    } catch {}
  }
  return { parseError: true, out: text.slice(0, 400) };
}

function runStrategy() {
  const childArgs = [
    script,
    "--amount",
    String(amount),
    "--bankroll",
    String(bankroll),
  ];
  if (live) childArgs.push("--live");
  try {
    const out = execFileSync(process.execPath, childArgs, {
      cwd: dir,
      env: childEnv,
      encoding: "utf8",
      timeout: 60000,
      maxBuffer: 2 * 1024 * 1024,
    });
    return parseOut(out);
  } catch (e) {
    const d = parseOut(e.stdout || "");
    if (!d.parseError) return d;
    return {
      parseError: true,
      status: e.status,
      out: String(e.stdout || "").slice(0, 400),
      err: String(e.stderr || e.message || "").slice(0, 400),
    };
  }
}

function readLedger() {
  try {
    if (existsSync(ledgerPath)) return JSON.parse(readFileSync(ledgerPath, "utf8"));
    if (existsSync(statePath)) return JSON.parse(readFileSync(statePath, "utf8"));
  } catch {}
  return null;
}

const startLine = JSON.stringify({
  start: ts(),
  amountPerLeg: amount,
  bankroll,
  intervalSec,
  rounds,
  live: false,
  liveFlagPassed: !!live,
  strategy: "human",
  feeFormula: "taker: usd*0.07*(1-p); maker/postOnly: 0",
  logFile,
});
console.log(startLine);
appendFileSync(logFile, startLine + "\n");

let tradeTicks = 0;
let lastD = null;

for (let i = 0; i < rounds; i++) {
  const d = runStrategy();
  lastD = d;
  const isTrade =
    d.action &&
    !["skip"].includes(d.action) &&
    Number(d.sizeUsd || 0) > 0;
  if (isTrade) tradeTicks++;

  const row = {
    t: ts(),
    round: i + 1,
    window: d.window ?? d.slug ?? null,
    remainingSec: d.remainingSec ?? null,
    regime: d.regime ?? null,
    action: d.action ?? null,
    side: d.side ?? null,
    price: d.price ?? null,
    sizeUsd: d.sizeUsd ?? null,
    feeUsd: d.feeUsd ?? 0,
    feeModel: d.feeModel ?? "maker0",
    netCostUsd: d.netCostUsd ?? null,
    expectedEdgeAfterFee: d.expectedEdgeAfterFee ?? null,
    feesPaid: d.feesPaid ?? null,
    realizedPnl: d.realizedPnl ?? null,
    equity: d.equity ?? null,
    cash: d.cash ?? null,
    reason: d.reason ?? null,
    gap: d.gap ?? null,
    up: d.up ?? null,
    down: d.down ?? null,
    live: false,
    ok: d.ok,
    raw: d.parseError ? d : undefined,
  };
  const line = JSON.stringify(row);
  console.log(line);
  appendFileSync(logFile, line + "\n");
  if (i < rounds - 1) execFileSync("sleep", [String(intervalSec)], { stdio: "ignore" });
}

const ledger = readLedger();
const fills = ledger?.fills?.length ?? lastD?.fills?.length ?? null;
const done = {
  done: ts(),
  rounds,
  logFile,
  tradeTicks,
  fills: fills,
  feesPaid: ledger?.feesPaid ?? lastD?.feesPaid ?? null,
  realizedPnl: ledger?.realizedPnl ?? lastD?.realizedPnl ?? null,
  mtmEquity: ledger?.equity ?? lastD?.equity ?? null,
  cash: ledger?.cash ?? lastD?.cash ?? null,
  bankroll: ledger?.bankroll ?? Number(bankroll),
  netPnl:
    ledger?.equity != null
      ? Number((Number(ledger.equity) - Number(ledger.bankroll || bankroll)).toFixed(6))
      : lastD?.equity != null
        ? Number((Number(lastD.equity) - Number(bankroll)).toFixed(6))
        : null,
  feeFormula: "taker: usd*0.07*(1-p); maker/postOnly: 0",
};
const doneLine = JSON.stringify(done);
console.log(doneLine);
appendFileSync(logFile, doneLine + "\n");
