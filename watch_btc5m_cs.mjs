#!/usr/bin/env node
import { execFileSync } from "child_process";
import { fileURLToPath } from "url";
import { dirname, join } from "path";
import { appendFileSync, mkdirSync } from "fs";

const dir = dirname(fileURLToPath(import.meta.url));
const script = join(dir, "strategy_btc5m_cs.mjs");
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
const minEdge = args["min-edge"] || "0.01";
const intervalSec = Number(args.interval || 15);
const rounds = Number(args.rounds || 40);
const live = args.live === "true" || args.live === "1";
const logDir = join(dir, "logs");
mkdirSync(logDir, { recursive: true });
const logFile = join(logDir, `watch_btc5m_${new Date().toISOString().replace(/[:.]/g, "-")}.jsonl`);

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
  try {
    return JSON.parse(text);
  } catch {}
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(text.slice(start, end + 1));
    } catch {}
  }
  return { parseError: true, out: text.slice(0, 400) };
}

function runStrategy(extra = []) {
  const childArgs = [script, "--amount", String(amount), "--min-edge", String(minEdge), ...extra];
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

function summarize(kind, d) {
  return {
    t: ts(),
    kind,
    slug: d.slug ?? null,
    upAsk: d.upAsk ?? null,
    downAsk: d.downAsk ?? null,
    askSum: d.askSum ?? null,
    edgeAsk: d.edgeAsk ?? null,
    bidSum: d.bidSum ?? null,
    edgeBid: d.edgeBid ?? null,
    action: d.action ?? null,
    reason: d.reason ?? null,
    expectedNet: d.expectedNet ?? null,
    makerExpectedNet: d.makerExpectedNet ?? null,
    shares: d.shares ?? null,
    makerShares: d.makerShares ?? null,
    amountCapPerLeg: d.amountCapPerLeg ?? null,
    mode: d.mode ?? null,
  };
}

console.log(JSON.stringify({ start: ts(), amountPerLeg: amount, minEdge, intervalSec, rounds, live: !!live, logFile }));

for (let i = 0; i < rounds; i++) {
  const ask = runStrategy([]);
  const maker = runStrategy(["--maker"]);
  const row = { t: ts(), round: i + 1, ask: summarize("ask", ask), maker: summarize("maker", maker) };
  const line = JSON.stringify(row);
  console.log(line);
  appendFileSync(logFile, line + "\n");
  if (i < rounds - 1) execFileSync("sleep", [String(intervalSec)], { stdio: "ignore" });
}
console.log(JSON.stringify({ done: ts(), rounds, logFile }));
