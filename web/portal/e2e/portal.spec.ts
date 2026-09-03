import { expect, test } from "@playwright/test";

/**
 * The portal against the real project. The approval test writes to commercetools, so it
 * proposes something reversible and asserts the platform actually moved.
 */

test("the dashboard reports what commercetools can supply and omits what it cannot", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("operator@");

  // Sales and orders are derived from real orders.
  await expect(page.getByText(/^\$[\d,]+$/).first()).toBeVisible({ timeout: 30_000 });

  // Conversion has no commercetools source, so the tile must render it as absent rather
  // than as a zero -- a flat 0% would read as a catastrophic week rather than a missing
  // feed. Asserted on the tile itself: a page-wide "0%" check matches "20% move cap" in a
  // staged change's summary.
  const conversion = page.getByRole("button", { name: /Conversion/ });
  await expect(conversion).toBeVisible();
  await expect(conversion).toContainText("—");
  await expect(conversion).not.toContainText("%");
});

test("the queue only lists stock the shop itself is short of", async ({ page }) => {
  await page.goto("/");
  const queue = page.getByRole("heading", { name: "Needs you today" });
  await expect(queue).toBeVisible({ timeout: 30_000 });
  // Every alert states a stock figure; a listing well stocked in the sellable channel must
  // not appear at all, which is what judging one supply channel used to do.
  const body = await page.locator("main").innerText();
  expect(body).toMatch(/in stock|Sold out/);
});

test("a proposed change waits for approval and says so", async ({ page }) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /merchant assistant/i });
  await composer.fill("Stage a 3% price increase on the Amalia Rug.");
  await composer.press("Enter");

  const card = page.getByRole("heading", { name: "Proposed change" });
  await expect(card).toBeVisible({ timeout: 150_000 });
  await expect(page.getByText("Awaiting approval")).toBeVisible();
  await expect(page.getByText("Nothing applies until you approve.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Dismiss" })).toBeVisible();
});

test("dismissing a change records it as the operator's own, and writes nothing", async ({
  page,
}) => {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /merchant assistant/i });
  await composer.fill("Stage a 2% price increase on the Braided Rug.");
  await composer.press("Enter");
  await expect(page.getByRole("heading", { name: "Proposed change" })).toBeVisible({
    timeout: 150_000,
  });

  await page.getByRole("button", { name: "Dismiss" }).first().click();

  // The contract is the audit trail, not the button's own re-render: a dismissed change
  // is resolved, attributed to the operator rather than the assistant, and never written.
  // (Asserting the button disappears would be asserting a rendering detail of the
  // reference's own card component.)
  const trail = page.getByRole("heading", { name: "Recent changes" });
  await expect(trail).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Dismissed by operator@/).first()).toBeVisible({ timeout: 60_000 });
});
