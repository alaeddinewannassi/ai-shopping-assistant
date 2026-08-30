import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { SEED_RESULT_PATH, type SeedResult } from "../config-values";

const seed: SeedResult = JSON.parse(readFileSync(SEED_RESULT_PATH, "utf-8"));
const [storeOne, storeTwo] = seed.stores;

test("widget demo page loads the real widget", async ({ page }) => {
  await page.goto(`http://localhost:4000/?apiBase=http://localhost:8000&tenantKey=${storeOne.widget_public_key}`);
  await expect(page.locator("assistant-chat-widget")).toBeAttached();
});

test("chatbot backend resolves each seeded tenant via its own X-Assistant-Key", async ({ request }) => {
  const respOne = await request.get("http://localhost:8000/health", {
    headers: { "X-Assistant-Key": storeOne.widget_public_key },
  });
  expect(respOne.ok()).toBeTruthy();
  expect((await respOne.json()).tenant).toBe(storeOne.slug);

  const respTwo = await request.get("http://localhost:8000/health", {
    headers: { "X-Assistant-Key": storeTwo.widget_public_key },
  });
  expect(respTwo.ok()).toBeTruthy();
  expect((await respTwo.json()).tenant).toBe(storeTwo.slug);
});

test("backoffice backend and frontend are reachable", async ({ request, page }) => {
  const health = await request.get("http://localhost:8001/health");
  expect(health.ok()).toBeTruthy();

  await page.goto("http://localhost:5173/");
  await expect(page.getByText("Backoffice login")).toBeVisible();
});
