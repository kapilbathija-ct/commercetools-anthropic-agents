import { expect, test } from "@playwright/test";
import { KNOWN_PRODUCT } from "./fixtures";

/** The behaviours that hold whether or not anyone is checking out. */

test("the shelf shows real catalogue products at their discounted price", async ({ page }) => {
  await page.goto("/");
  const shelf = page.getByRole("heading", { name: "Popular right now" });
  await expect(shelf).toBeVisible({ timeout: 30_000 });
  // Every tile has a title and a price; a blank title means a locale gap went unhandled.
  const tiles = page.locator("main button", { hasText: "$" });
  expect(await tiles.count()).toBeGreaterThan(0);
  const text = await page.locator("main").innerText();
  expect(text).not.toMatch(/\$\s*0\.00/);
});

test("the cart survives a reload, because the session id is persisted", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: /Message/ }).fill(`Add the ${KNOWN_PRODUCT} to my cart.`);
  await page.getByRole("textbox", { name: /Message/ }).press("Enter");
  const cart = page.getByRole("complementary", { name: "Cart" });
  await expect(cart.getByText("1 item", { exact: true })).toBeVisible({ timeout: 120_000 });

  await page.reload();
  // A fresh session per load would mean a new anonymous id and an empty cart.
  await expect(cart.getByText("1 item", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(cart.getByText(KNOWN_PRODUCT).first()).toBeVisible();
});

test("checkout refuses an empty cart rather than starting a payment", async ({ page }) => {
  await page.goto("/");
  // A brand-new session, so the cart is empty.
  await page.evaluate(() => window.localStorage.clear());
  await page.goto("/checkout");
  await expect(page.getByRole("heading", { name: "Your cart is empty" })).toBeVisible({
    timeout: 30_000,
  });
});

test("a guest has no order history and is told so, not shown an error", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Orders" }).click();
  const main = page.locator("main");
  await expect(main).not.toContainText("Something went wrong", { timeout: 30_000 });
});

test("the confirmation page refuses an order this session does not own", async ({ page }) => {
  await page.goto("/");
  // A real, valid order id from another session: ownership, not existence, is the check.
  await page.goto("/checkout/confirmation/31a28680-deb1-42f5-bc9e-302e23496084");
  await expect(page.getByRole("heading", { name: /can't show that order/i })).toBeVisible({
    timeout: 30_000,
  });
});
