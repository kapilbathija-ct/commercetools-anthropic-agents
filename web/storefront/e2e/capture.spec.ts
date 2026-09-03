import { expect, test } from "@playwright/test";
import { ADDRESS, KNOWN_PRODUCT, STRIPE_TEST_CARD, uniqueEmail } from "./fixtures";

const SHOTS = "../../docs/screenshots";

/** Runs the real paid flow and captures the two screens Chrome MCP cannot reach. */
test("capture the payment step and the confirmation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: /Message/ }).fill(`Add the ${KNOWN_PRODUCT} to my cart.`);
  await page.getByRole("textbox", { name: /Message/ }).press("Enter");
  const cart = page.getByRole("complementary", { name: "Cart" });
  await expect(cart.getByText("1 item", { exact: true })).toBeVisible({ timeout: 120_000 });

  await page.goto("/checkout");
  await page.getByRole("textbox", { name: "First name" }).fill("Kapil");
  await page.getByRole("textbox", { name: "Last name" }).fill("Bathija");
  await page.getByRole("textbox", { name: "Email" }).fill(uniqueEmail());
  await page.getByRole("textbox", { name: "Street address" }).fill("1 Market Street");
  await page.getByRole("textbox", { name: "City" }).fill(ADDRESS.city);
  await page.getByRole("textbox", { name: "State" }).fill(ADDRESS.state);
  await page.getByRole("textbox", { name: "Postal code" }).fill(ADDRESS.postalCode);
  await page.getByRole("button", { name: "Save address" }).click();

  await expect(page.getByText("Delivery method")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("radio", { name: /Economy Ground Shipping/ }).click();
  await page.getByRole("button", { name: "Continue to payment" }).click();
  await expect(page.getByText("Total", { exact: true })).toBeVisible({ timeout: 60_000 });

  const stripe = page.frameLocator('iframe[title="Secure payment input frame"]');
  await stripe.getByLabel("Card number").fill(STRIPE_TEST_CARD.number, { timeout: 90_000 });
  await stripe.getByLabel(/Expiration/i).fill(STRIPE_TEST_CARD.expiry);
  await stripe.getByRole("textbox", { name: "Security code" }).fill(STRIPE_TEST_CARD.cvc);
  const zip = stripe.getByLabel("ZIP code");
  if (await zip.count()) await zip.fill(STRIPE_TEST_CARD.zip);

  // The card fields filled in, before submitting.
  await page.screenshot({ path: `${SHOTS}/07b-stripe-card-filled.png` });

  await page.getByRole("button", { name: /COMPLETE PURCHASE/i }).click();
  await page.waitForURL(/\/checkout\/confirmation\//, { timeout: 150_000 });
  await expect(page.getByRole("heading", { name: "Order confirmed" })).toBeVisible();
  await expect(page.getByTestId("payment-state")).toHaveText("paid", { timeout: 30_000 });
  await page.screenshot({ path: `${SHOTS}/08-order-confirmed.png` });

  console.log(
    `order ${await page.getByTestId("order-id").textContent()} ` +
      `total ${await page.getByTestId("order-total").textContent()}`,
  );
});
