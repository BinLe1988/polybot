/** Shared .env loader for poly-test live helpers. Never logs secrets. */
import { readFileSync, existsSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

export function loadEnv(envPath) {
  const path = envPath || join(dirname(fileURLToPath(import.meta.url)), ".env");
  if (!existsSync(path)) throw new Error(`missing .env at ${path}`);
  const fileEnv = Object.fromEntries(
    readFileSync(path, "utf8")
      .split(/\n/)
      .filter((l) => l && !l.startsWith("#") && l.includes("="))
      .map((l) => {
        const i = l.indexOf("=");
        return [l.slice(0, i).trim(), l.slice(i + 1).trim()];
      })
  );
  // process.env wins so GUIDED_LIVE launches can override without editing .env
  const env = { ...fileEnv, ...Object.fromEntries(
    Object.entries(process.env).filter(([k, v]) => k.startsWith("POLYMARKET_") && v != null && v !== "")
  ) };

  let pk = (env.POLYMARKET_PRIVATE_KEY || "").trim();
  if (!pk) throw new Error("POLYMARKET_PRIVATE_KEY missing");
  if (!pk.startsWith("0x")) pk = "0x" + pk;

  const wallet =
    env.POLYMARKET_WALLET_ADDRESS ||
    "0xD18b881EA7babdDeB8d1cd01C309b9769FaF49B5";
  const funder =
    env.POLYMARKET_FUNDER_ADDRESS ||
    env.POLYMARKET_WALLET_ADDRESS ||
    wallet;

  let signatureType = env.POLYMARKET_SIGNATURE_TYPE ?? 1;
  const st = String(signatureType).trim().toUpperCase();
  if (st === "POLY_PROXY" || st === "PROXY") signatureType = 1;
  else if (st === "EOA") signatureType = 0;
  else if (st === "POLY_GNOSIS_SAFE" || st === "GNOSIS_SAFE" || st === "SAFE")
    signatureType = 2;
  else if (st === "POLY_1271" || st === "1271") signatureType = 3;
  else signatureType = Number(signatureType);
  if (!Number.isFinite(signatureType)) signatureType = 3;

  return {
    privateKey: pk,
    wallet,
    funder,
    maxOrderUsd: Number(env.POLYMARKET_MAX_ORDER_USD || 10),
    live: String(env.POLYMARKET_LIVE || "0") === "1",
    signatureType,
  };
}

export function parseArgs(argv = process.argv.slice(2)) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith("--")) {
      const k = argv[i].slice(2);
      const v = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : "true";
      args[k] = v;
    }
  }
  return args;
}
