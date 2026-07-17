import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function login(page: Page, code = "D001") {
  await setClock(page, "2026-08-10T12:00:00+01:00");
  await page.goto("/login");
  await page.getByLabel("Player code").fill(code);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/$/);
}

async function setClock(page: Page, now: string) {
  const response = await page.request.post("/__test__/clock", {
    headers: { "x-season27-test-token": "local-browser-test-token" },
    data: { now },
  });
  expect(response.ok()).toBeTruthy();
}

test("core lifecycle remains usable across time boundaries", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await login(page, "D002");
  await expect(page.getByText(/predictions open/i).first()).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/prediction");
  await expect(page.getByRole("heading").first()).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  const moveButton = page.locator("button.move-button:not(:disabled)").first();
  await moveButton.focus();
  await expect(moveButton).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("link", { name: /review and submit/i }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: /submit prediction/i }).click();
  await setClock(page, "2026-09-15T12:00:00+01:00");
  await page.goto("/");
  await expect(page.getByText(/swap 1 open/i).first()).toBeVisible();
  await page.goto("/leaderboard");
  await expect(page.getByRole("heading").first()).toBeVisible();
});
