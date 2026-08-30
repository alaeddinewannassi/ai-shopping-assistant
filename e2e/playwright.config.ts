import { defineConfig } from "@playwright/test";
import {
  APP_ENCRYPTION_KEY,
  BACKOFFICE_BACKEND_DIR,
  BACKOFFICE_BACKEND_PYTHON,
  BACKOFFICE_FRONTEND_DIR,
  CHATBOT_BACKEND_DIR,
  CHATBOT_BACKEND_PYTHON,
  DATABASE_URL,
  JWT_SECRET,
  ROOT,
} from "./config-values";

/**
 * Orchestrates the full-journey E2E test. Seeding happens BEFORE this config is even
 * loaded — `npm test`'s `pretest` step runs scripts/seed-db.mjs as a separate process that
 * fully exits first (see that file for why: Playwright starts `webServer` processes before
 * running `globalSetup` in this version, so a backend that opens its SQLite connection
 * before the file is wiped-and-recreated keeps reading the deleted-but-still-open inode
 * forever — pretest-seeding sidesteps the ordering question entirely).
 *
 * `reuseExistingServer` is hardcoded `false` (not the usual `!CI` convention) for the same
 * reason: reusing a server left running from a previous invocation would mean it's still
 * holding a connection from the OLD seed, not the one pretest just created.
 *
 * The journey test itself (tests/multi-tenant-journey.spec.ts) skips without a real
 * `LLM_API_KEY` (Groq) — see e2e/README.md.
 */

const sharedBackendEnv = { ...process.env, DATABASE_URL, APP_ENCRYPTION_KEY };

// Slows down every Playwright action (click, fill, navigation) — and, in
// multi-tenant-journey.spec.ts, adds character-by-character typing and multi-second reading
// pauses — when E2E_SLOW=1 is set, so a human watching a --headed run can actually follow
// the conversation. A real run otherwise finishes in well under 30 seconds, too fast to read.
//
// Deliberately an explicit env var the invoker sets (`E2E_SLOW=1 npm test -- --headed`),
// NOT auto-detected from `--headed` in process.argv or propagated via an in-process
// `process.env` mutation here: Playwright's test-execution worker is a genuinely separate
// process from the one that evaluates this config file, and does not reliably inherit
// either the original CLI argv or a same-process env mutation made after that worker was
// already spawned (verified empirically — a mutation here was visible in one process's
// module evaluation and silently absent in the one that actually ran the tests). A plain
// shell env var set before `npm test` even starts has no such timing dependency: it's part
// of the process tree's environment from the very first process onward.
const isSlow = process.env.E2E_SLOW === "1";

export default defineConfig({
  testDir: "./tests",
  // A full multi-turn conversation with a real LLM in the loop (plus the Phase 5 backoffice
  // tail) needs more headroom than a typical UI test — each turn is a real network call, and
  // --headed runs add several seconds of deliberate human-watchable pauses per turn on top
  // of that (see multi-tenant-journey.spec.ts and slowMo below) — this can run several
  // minutes end to end when headed.
  timeout: 300_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    launchOptions: { slowMo: isSlow ? 700 : 0 },
  },
  webServer: [
    {
      // `python -m uvicorn`, not the `uvicorn` console-script — venv console scripts bake
      // in an absolute interpreter shebang at creation time, which breaks if the venv was
      // ever created under a different path (as this repo's venvs were, pre-rename).
      command: `${CHATBOT_BACKEND_PYTHON} -m uvicorn src.api.chat:app --port 8000`,
      cwd: CHATBOT_BACKEND_DIR,
      url: "http://localhost:8000/health",
      reuseExistingServer: false,
      env: sharedBackendEnv,
      timeout: 30_000,
    },
    {
      command: `python3 -m http.server 4000 --directory widget-demo`,
      cwd: ROOT,
      url: "http://localhost:4000",
      reuseExistingServer: false,
      timeout: 15_000,
    },
    {
      command: `${BACKOFFICE_BACKEND_PYTHON} -m uvicorn src.api.main:app --port 8001`,
      cwd: BACKOFFICE_BACKEND_DIR,
      url: "http://localhost:8001/health",
      reuseExistingServer: false,
      env: { ...sharedBackendEnv, JWT_SECRET, ADMIN_CORS_ORIGINS: "http://localhost:5173", COOKIE_SECURE: "false" },
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --port 5173 --strictPort",
      cwd: BACKOFFICE_FRONTEND_DIR,
      url: "http://localhost:5173",
      reuseExistingServer: false,
      env: { ...process.env, VITE_API_BASE: "http://localhost:8001" },
      timeout: 30_000,
    },
  ],
});
