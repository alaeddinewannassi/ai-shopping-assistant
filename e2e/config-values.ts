import path from "node:path";

/**
 * Pure, side-effect-free shared values — safe to import from playwright.config.ts (which
 * Playwright loads multiple times across its orchestrator and worker processes) AND from
 * tests/*.spec.ts. The actual seeding work (wiping .tmp, running seed.py) lives in
 * scripts/seed-db.mjs, run as a plain `pretest` npm step BEFORE `playwright test` is even
 * invoked — never here, and never as Playwright's own `globalSetup` (see that script's own
 * header comment for why: this version of Playwright starts `webServer` processes before
 * running `globalSetup`, so a backend would open its SQLite connection before the file is
 * wiped-and-recreated and keep reading the deleted-but-still-open inode forever).
 */

export const ROOT = __dirname;
export const TMP_DIR = path.join(ROOT, ".tmp");
export const DB_PATH = path.join(TMP_DIR, "e2e.db");
export const SEED_RESULT_PATH = path.join(TMP_DIR, "seed-result.json");

export const DATABASE_URL = `sqlite:///${DB_PATH}`;

// Fixed, not random: this is a throwaway local database recreated fresh by global-setup.ts
// on every run — there's no secret worth protecting across runs, only a need for every
// process in THIS run (seed.py, the chatbot backend, the backoffice backend) to agree on
// the same value, which a fixed constant guarantees trivially.
export const APP_ENCRYPTION_KEY = "e2e-test-only-encryption-key-not-for-production-use";
export const JWT_SECRET = "e2e-test-only-jwt-secret-not-for-production-use";

export const LLM_API_KEY = process.env.LLM_API_KEY || "no-key-configured-see-e2e-readme";
export const LLM_MODEL = process.env.LLM_MODEL || "llama-3.3-70b-versatile";

export const CHATBOT_BACKEND_DIR = path.join(ROOT, "..", "chatbot", "backend");
export const BACKOFFICE_BACKEND_DIR = path.join(ROOT, "..", "backoffice", "backend");
export const BACKOFFICE_FRONTEND_DIR = path.join(ROOT, "..", "backoffice", "frontend");

export const CHATBOT_BACKEND_PYTHON = path.join(CHATBOT_BACKEND_DIR, ".venv", "bin", "python");
export const BACKOFFICE_BACKEND_PYTHON = path.join(BACKOFFICE_BACKEND_DIR, ".venv", "bin", "python");

export interface SeededStore {
  slug: string;
  tenant_id: string;
  widget_public_key: string;
  admin_email: string;
  admin_password: string;
}

export interface MultiTenantAdmin {
  admin_email: string;
  admin_password: string;
}

export interface SeedResult {
  stores: SeededStore[];
  multi_admin: MultiTenantAdmin;
}
