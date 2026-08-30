#!/usr/bin/env node
/**
 * Wipes and reseeds the throwaway E2E database — run as an npm `pretest` step, BEFORE
 * `playwright test` is invoked at all, not as Playwright's own `globalSetup`.
 *
 * Why not globalSetup: Playwright starts `webServer` processes before running
 * globalSetup in this version (verified empirically — see the `[global-setup] START`
 * timestamp printing *after* "Uvicorn running" in a debug run). A backend that opens its
 * SQLite connection before the file is wiped-and-recreated keeps reading the deleted-but-
 * still-open inode forever, never seeing the freshly seeded data. Seeding here, as a
 * separate process that fully exits before `playwright test` launches anything, guarantees
 * no server ever touches the database before it's real.
 *
 * Plain JS, not TypeScript: this must run via a bare `node`, before any Playwright/ts-node
 * machinery exists — duplicates a handful of literal constants from ../config-values.ts
 * (kept in sync manually; small enough that it's not worth a build step).
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const TMP_DIR = path.join(ROOT, ".tmp");
const DB_PATH = path.join(TMP_DIR, "e2e.db");
const SEED_RESULT_PATH = path.join(TMP_DIR, "seed-result.json");

// Keep these three literals identical to config-values.ts.
const DATABASE_URL = `sqlite:///${DB_PATH}`;
const APP_ENCRYPTION_KEY = "e2e-test-only-encryption-key-not-for-production-use";
const LLM_API_KEY = process.env.LLM_API_KEY || "no-key-configured-see-e2e-readme";
const LLM_MODEL = process.env.LLM_MODEL || "openai/gpt-oss-120b";

const BACKOFFICE_BACKEND_PYTHON = path.join(ROOT, "..", "backoffice", "backend", ".venv", "bin", "python");

rmSync(TMP_DIR, { recursive: true, force: true });
mkdirSync(TMP_DIR, { recursive: true });

if (!existsSync(path.join(ROOT, "widget-demo", "assistant-widget.js"))) {
  throw new Error("widget-demo/assistant-widget.js is missing — run `npm run build:widget` first.");
}

const seedOutput = execFileSync(BACKOFFICE_BACKEND_PYTHON, ["seed.py"], {
  cwd: ROOT,
  env: { ...process.env, DATABASE_URL, APP_ENCRYPTION_KEY, LLM_API_KEY, LLM_MODEL },
  encoding: "utf-8",
});

const resultLine = seedOutput.split("\n").find((line) => line.startsWith("SEED_RESULT: "));
if (!resultLine) {
  throw new Error(`seed.py produced no SEED_RESULT line. Full output:\n${seedOutput}`);
}
const seed = JSON.parse(resultLine.slice("SEED_RESULT: ".length));
writeFileSync(SEED_RESULT_PATH, JSON.stringify(seed, null, 2));
for (const store of seed.stores) {
  console.log(`Seeded ${store.slug}, widget key ${store.widget_public_key}`);
}
