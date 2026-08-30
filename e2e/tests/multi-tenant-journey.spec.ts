import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { SEED_RESULT_PATH, type SeededStore, type SeedResult } from "../config-values";

/**
 * Two different shopping websites, two different real conversations, then one backoffice
 * check per tenant proving each admin sees only their own store's analytics — the full
 * multi-tenancy story (specs/002-backoffice-analytics), driven end to end with a real Groq
 * model, not the scripted `rule-based-stub` phrasing every other test in this repo uses.
 *
 * Between the two stores, every one of the fixed 9 action types
 * (chatbot/backend/src/agent/llm_client.py's tool schema) gets exercised at least once:
 * search_products, navigate_to, propose_add_to_cart, propose_update_cart,
 * propose_remove_from_cart, apply_promo, request_checkout, confirm_pending_action,
 * decline_pending_action.
 *
 * Skips without a real LLM_API_KEY (Groq), mirroring this repo's existing pattern for an
 * opt-in real-dependency test (tests/contract/test_adapter_contract_prestashop.py in
 * chatbot/backend, which auto-skips unless a live PrestaShop store is configured).
 *
 * The 4 tests below MUST run in this order and in the same worker (test.describe.serial) —
 * the backoffice checks read data the store tests just created.
 *
 * `--headed`: typing is simulated character-by-character and every step pauses for several
 * seconds, so a human watching the browser can actually follow the conversation and the
 * backoffice walkthrough — a real run otherwise finishes in well under 30 seconds, far too
 * fast to read. This whole file's pacing is a no-op in headless runs (nothing to watch).
 */
test.skip(!process.env.LLM_API_KEY, "requires a real Groq LLM_API_KEY — see e2e/README.md");

const seed: SeedResult = JSON.parse(readFileSync(SEED_RESULT_PATH, "utf-8"));
const [storeOne, storeTwo] = seed.stores;
const { multi_admin: multiAdmin } = seed;

const SESSION_ID_ONE = `e2e-store-one-${Date.now()}`;
const SESSION_ID_TWO = `e2e-store-two-${Date.now()}`;

// An explicit shell env var, not auto-detected — see playwright.config.ts's E2E_SLOW
// comment for why (this file's module is evaluated in a different process than the one
// that runs the tests; an in-process mutation doesn't reliably reach both).
const isSlow = process.env.E2E_SLOW === "1";
const READING_PAUSE_MS = 3_000;
const TYPING_DELAY_MS = 45; // per character — matches a fast-but-visible human typist

async function pauseForHuman(page: Page, ms = READING_PAUSE_MS): Promise<void> {
  if (isSlow) await page.waitForTimeout(ms);
}

function widget(page: Page) {
  return page.locator("assistant-chat-widget");
}

/** Types a natural-language message character-by-character (headed) or instantly
 * (headless), submits it, and waits for the assistant's reply — the reply can take a few
 * seconds since it's a real network call to Groq. Returns the reply text so the test can
 * assert on it, and pauses afterward so a human has time to read it. */
async function sendMessage(page: Page, text: string): Promise<string> {
  const assistantMessages = widget(page).locator(".message.assistant");
  const countBefore = await assistantMessages.count();
  const input = widget(page).locator("input");

  if (isSlow) {
    await input.pressSequentially(text, { delay: TYPING_DELAY_MS });
    await pauseForHuman(page, 600); // a beat between finishing typing and hitting send
  } else {
    await input.fill(text);
  }
  await widget(page).locator("button[type=submit]").click();

  await expect(assistantMessages).toHaveCount(countBefore + 1, { timeout: 20_000 });
  const reply = (await assistantMessages.last().textContent()) ?? "";
  await pauseForHuman(page);
  return reply;
}

async function openWidgetFor(page: Page, store: SeededStore, sessionId: string): Promise<void> {
  await page.goto(
    `http://localhost:4000/?apiBase=http://localhost:8000&tenantKey=${store.widget_public_key}&sessionId=${sessionId}`,
  );
  await pauseForHuman(page);
  await page.getByRole("button", { name: "Open chat" }).click();
  await pauseForHuman(page, 1_200);
}

async function loginToBackoffice(page: Page, email: string, password: string): Promise<void> {
  await page.goto("http://localhost:5173/");
  await pauseForHuman(page);
  const emailInput = page.getByLabel("Email");
  const passwordInput = page.getByLabel("Password");
  if (isSlow) {
    await emailInput.pressSequentially(email, { delay: TYPING_DELAY_MS });
    await pauseForHuman(page, 500);
    await passwordInput.pressSequentially(password, { delay: TYPING_DELAY_MS });
    await pauseForHuman(page, 500);
  } else {
    await emailInput.fill(email);
    await passwordInput.fill(password);
  }
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.locator("main").getByRole("heading", { name: "Overview" })).toBeVisible();
  await pauseForHuman(page, READING_PAUSE_MS * 2); // the overview stat tiles are worth lingering on
}

/** Clicks a nav link and pauses before AND after, so a human sees the click register and
 * then has time to read whatever the new page shows before the test's own assertions
 * (which don't pause) start checking it. */
async function goToBackofficePage(page: Page, linkName: string): Promise<void> {
  await pauseForHuman(page, 1_000);
  await page.getByRole("link", { name: linkName }).click();
  await pauseForHuman(page, READING_PAUSE_MS);
}

test.describe.serial("multi-tenant shopping journey", () => {
  test("Store One: full purchase — discovery, navigation, cart, quantity change, promo, checkout", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await openWidgetFor(page, storeOne, SESSION_ID_ONE);

    // 1. Discovery.
    let reply = await sendMessage(page, "I'm looking for a comfortable t-shirt in red");
    expect(reply.toLowerCase()).toContain("shirt");

    // 2. Navigate to a category.
    reply = await sendMessage(page, "actually, can you show me the jackets category?");
    expect(reply.toLowerCase()).toContain("jacket");

    // 3. Add to cart. Names the product explicitly — CartIntentHandler.resolve_add_to_cart()
    // does a fresh keyword search against the store on every turn with no memory of prior
    // turns (research.md §9.4: references bind to a concrete product or force a pick-list,
    // never carried across turns), so the product must be named in this message itself.
    reply = await sendMessage(page, "add the red classic t-shirt to my cart please");
    expect(reply.toLowerCase()).toContain("confirm");

    // 4. Confirm the add.
    reply = await sendMessage(page, "yes, add it");
    expect(reply).toContain("Classic T-Shirt");

    // 5. Change the quantity.
    reply = await sendMessage(page, "actually, update the t-shirt quantity to 2");
    expect(reply.toLowerCase()).toContain("confirm");

    // 6. Confirm the update.
    reply = await sendMessage(page, "yes");
    expect(reply).toContain("2 x Classic T-Shirt");

    // 7. Ask about promos — the seeded welcome10 rule (first order) should fire.
    reply = await sendMessage(page, "do I have any discounts available?");
    expect(reply.toUpperCase()).toContain("WELCOME10");

    // 8. Confirm the promo.
    reply = await sendMessage(page, "yes, apply it");
    expect(reply.toLowerCase()).not.toContain("don't see any discounts");

    // 9. Checkout.
    reply = await sendMessage(page, "I'd like to check out now");
    expect(reply.toLowerCase()).toContain("confirm");

    // 10. Confirm the order.
    reply = await sendMessage(page, "yes, place the order");
    expect(reply).toContain("Order placed!");
    await pauseForHuman(page, READING_PAUSE_MS * 2); // "Order placed!" deserves a moment

    expect(consoleErrors, `Unexpected console errors: ${consoleErrors.join("\n")}`).toEqual([]);
  });

  test("Store Two: browses, declines a proposal, adds then removes an item — abandons before checkout", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(msg.text());
    });

    await openWidgetFor(page, storeTwo, SESSION_ID_TWO);

    // 1. Discovery.
    let reply = await sendMessage(page, "I'm looking for a blue jacket");
    expect(reply.toLowerCase()).toContain("jacket");

    // 2. Propose adding it. Size specified up front — the Blue Jacket has two size variants
    // (M in stock, L out of stock), and this system's ambiguity handling (research.md §9.4)
    // asks a clarifying question rather than guess, but has no memory of THIS reply to
    // interpret a bare follow-up like "the medium one" against (each turn re-resolves from
    // scratch) — a real shopper re-states the full request with the missing detail, same as
    // here, rather than the assistant chaining a multi-turn disambiguation dialogue.
    reply = await sendMessage(page, "add the blue jacket in size M to my cart please");
    expect(reply.toLowerCase()).toContain("confirm");

    // 3. Decline it — a genuine change of mind.
    reply = await sendMessage(page, "no, never mind");
    expect(reply.toLowerCase()).toContain("won't make that change");

    // 4. Propose it again.
    reply = await sendMessage(page, "actually, go ahead and add the blue jacket in size M to my cart");
    expect(reply.toLowerCase()).toContain("confirm");

    // 5. Confirm this time.
    reply = await sendMessage(page, "yes, add it");
    expect(reply).toContain("Blue Jacket");

    // 6. Remove it.
    reply = await sendMessage(page, "actually, remove the jacket from my cart");
    expect(reply.toLowerCase()).toContain("confirm");

    // 7. Confirm the removal — cart ends up empty, no checkout attempted at all.
    reply = await sendMessage(page, "yes");
    expect(reply.toLowerCase()).toContain("cart is now empty");
    await pauseForHuman(page, READING_PAUSE_MS * 2);

    expect(consoleErrors, `Unexpected console errors: ${consoleErrors.join("\n")}`).toEqual([]);
  });

  test("Backoffice: Store One's admin sees only Store One's session, ordered at 100% conversion", async ({
    page,
  }) => {
    await loginToBackoffice(page, storeOne.admin_email, storeOne.admin_password);
    const main = page.locator("main");

    await goToBackofficePage(page, "Sessions");
    await expect(main.getByRole("link", { name: SESSION_ID_ONE })).toBeVisible();
    await expect(main.getByRole("link", { name: SESSION_ID_TWO })).not.toBeVisible();
    await pauseForHuman(page);

    await main.getByRole("link", { name: SESSION_ID_ONE }).click();
    await pauseForHuman(page, 1_000);
    // .first(): SessionDetail.tsx renders both an event's intent and action in separate
    // divs, and for a search_products event intent==action, so this matches twice.
    await expect(main.getByText("search_products").first()).toBeVisible();
    await expect(main.getByText("navigate_to").first()).toBeVisible();
    await expect(main.getByText("propose_update_cart").first()).toBeVisible();
    await pauseForHuman(page, READING_PAUSE_MS * 2); // the full event replay is worth lingering on

    // This fresh, throwaway database has exactly one session on this tenant, and it
    // ordered — so these numbers are exact, not just "non-zero".
    await goToBackofficePage(page, "Overview");
    const sessionsTile = main.getByText("Sessions", { exact: true }).locator("..");
    await expect(sessionsTile).toContainText("1");
    const conversionTile = main.getByText("Conversion rate", { exact: true }).locator("..");
    await expect(conversionTile).toContainText("100.0%");
    await pauseForHuman(page, READING_PAUSE_MS * 2);

    await goToBackofficePage(page, "Funnel");
    const orderedRow = main.getByText("Ordered", { exact: true }).locator("..");
    await expect(orderedRow).toContainText("1 (100%)");
    await pauseForHuman(page, READING_PAUSE_MS * 2);
  });

  test("Backoffice: Store Two's admin sees only Store Two's session, never ordered", async ({ page }) => {
    await loginToBackoffice(page, storeTwo.admin_email, storeTwo.admin_password);
    const main = page.locator("main");

    await goToBackofficePage(page, "Sessions");
    await expect(main.getByRole("link", { name: SESSION_ID_TWO })).toBeVisible();
    await expect(main.getByRole("link", { name: SESSION_ID_ONE })).not.toBeVisible();
    await pauseForHuman(page);

    await main.getByRole("link", { name: SESSION_ID_TWO }).click();
    await pauseForHuman(page, 1_000);
    await expect(main.getByText("decline_pending_action").first()).toBeVisible();
    await expect(main.getByText("propose_remove_from_cart").first()).toBeVisible();
    await pauseForHuman(page, READING_PAUSE_MS * 2);

    // Store Two's session added then removed an item and never checked out — 0% conversion,
    // exact (not just "non-zero") since this tenant has exactly one session.
    await goToBackofficePage(page, "Overview");
    const sessionsTile = main.getByText("Sessions", { exact: true }).locator("..");
    await expect(sessionsTile).toContainText("1");
    const conversionTile = main.getByText("Conversion rate", { exact: true }).locator("..");
    await expect(conversionTile).toContainText("0.0%");
    await pauseForHuman(page, READING_PAUSE_MS * 2);

    await goToBackofficePage(page, "Funnel");
    const orderedRow = main.getByText("Ordered", { exact: true }).locator("..");
    await expect(orderedRow).toContainText("0 (0%)");
    await pauseForHuman(page, READING_PAUSE_MS * 2);
  });

  test("Backoffice: one admin login switches between both stores via the tenant switcher", async ({
    page,
  }) => {
    // Complements the two isolation tests above: those prove a scoped admin can only ever
    // see their own tenant. This proves the OTHER half of the multi-tenancy story — one
    // backoffice login legitimately managing multiple websites — via the multi-tenant admin
    // seeded with membership on BOTH stores (e2e/seed.py's _seed_multi_tenant_admin).
    await loginToBackoffice(page, multiAdmin.admin_email, multiAdmin.admin_password);
    const main = page.locator("main");

    const switcher = page.getByRole("combobox");
    await expect(switcher).toBeVisible();

    // The switcher must show real store names, not raw tenant ids — a human operator can't
    // otherwise tell which option is which website.
    const optionTexts = await switcher.locator("option").allTextContents();
    expect(optionTexts.some((t) => t.includes("E2E Store One"))).toBe(true);
    expect(optionTexts.some((t) => t.includes("E2E Store Two"))).toBe(true);

    // Select Store One and confirm the exact numbers from the first test above.
    await switcher.selectOption({ label: optionTexts.find((t) => t.includes("E2E Store One"))! });
    await pauseForHuman(page, READING_PAUSE_MS);
    await goToBackofficePage(page, "Sessions");
    await expect(main.getByRole("link", { name: SESSION_ID_ONE })).toBeVisible();
    await expect(main.getByRole("link", { name: SESSION_ID_TWO })).not.toBeVisible();
    await pauseForHuman(page, READING_PAUSE_MS);

    // Switch to Store Two — same login, same browser session, no re-authentication — and
    // confirm the view flips entirely to the other store's data.
    await switcher.selectOption({ label: optionTexts.find((t) => t.includes("E2E Store Two"))! });
    await pauseForHuman(page, READING_PAUSE_MS);
    await goToBackofficePage(page, "Sessions");
    await expect(main.getByRole("link", { name: SESSION_ID_TWO })).toBeVisible();
    await expect(main.getByRole("link", { name: SESSION_ID_ONE })).not.toBeVisible();
    await pauseForHuman(page, READING_PAUSE_MS * 2);
  });
});
