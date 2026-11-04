#!/usr/bin/env node
/** Summarize btc5m human dry-run from ledger + optional jsonl. */
import { readFileSync, existsSync, readdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const dir = dirname(fileURLToPath(import.meta.url));
const logDir = join(dir, "logs");
const ledgerPath = join(logDir, "btc5m_human_ledger.json");
const statePath = join(logDir, "btc5m_human_state.json");

function load(p) {
  if (!existsSync(p)) return null;
  return JSON.parse(readFileSync(p, "utf8"));
}

const ledger = load(ledgerPath) || load(statePath) || {};
const bankroll = Number(ledger.bankroll ?? 20);
const equity = Number(ledger.equity ?? bankroll);
const fills = ledger.fills || [];
const settlements = ledger.settlements || [];

let jsonl = process.argv[2];
if (!jsonl) {
  const files = readdirSync(logDir)
    .filter((f) => f.startsWith("watch_btc5m_human_") && f.endsWith(".jsonl"))
    .map((f) => join(logDir, f))
    .sort();
  jsonl = files[files.length - 1];
}

let rounds = null;
let done = null;
if (jsonl && existsSync(jsonl)) {
  const lines = readFileSync(jsonl, "utf8").trim().split("\n").filter(Boolean);
  for (const line of lines) {
    try {
      const o = JSON.parse(line);
      if (o.done) done = o;
      if (o.round) rounds = o.round;
    } catch {}
  }
}

const summary = {
  logFile: jsonl || null,
  rounds: done?.rounds ?? rounds,
  fills: fills.length,
  feesPaid: ledger.feesPaid ?? 0,
  realizedPnl: ledger.realizedPnl ?? 0,
  mtmEquity: equity,
  cash: ledger.cash ?? null,
  bankroll,
  netPnl: Number((equity - bankroll).toFixed(6)),
  settlements: settlements.length,
  feeFormula: ledger.feeFormula || "taker: usd*0.07*(1-p); maker/postOnly: 0",
  position: ledger.position || null,
};
console.log(JSON.stringify(summary, null, 2));
