import { expect, test } from "@playwright/test";
import { ADDRESS, KNOWN_PRODUCT, STRIPE_DECLINED_CARD, uniqueEmail } from "./fixtures";

/**
 * A declined card must fail visibly and leave no paid order behind. This is the case that
 * matters most for trust: a silent failure here looks identical to a silent success, since
 * `paymentFlow` navigates on neither by itself.
 */
test("a declined card shows an error and does not reach the confirmation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: /Message/ }).fill(`Add the ${KNOWN_PRODUCT} to my cart.`);
  await page.getByRole("textbox", { name: /Message/ }).press("Enter");
  const cart = page.getByRole("complementary", { name: "Cart" });
  await expect(cart.getByText("1 item", { exact: true })).toBeVisible({ timeout: 120_000 });

  await page.goto("/checkout");
  await page.getByRole("textbox", { name: "First name" }).fill(ADDRESS.firstName);
  await page.getByRole("textbox", { name: "Last name" }).fill(ADDRESS.lastName);
  await page.getByRole("textbox", { name: "Email" }).fill(uniqueEmail());
  await page.getByRole("textbox", { name: "Street address" }).fill(ADDRESS.street);
  await page.getByRole("textbox", { name: "City" }).fill(ADDRESS.city);
  await page.getByRole("textbox", { name: "State" }).fill(ADDRESS.state);
  await page.getByRole("textbox", { name: "Postal code" }).fill(ADDRESS.postalCode);
  await page.getByRole("button", { name: "Save address" }).click();

  await expect(page.getByText("Delivery method")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("radio", { name: /In Store Pickup/ }).click();
  await page.getByRole("button", { name: "Continue to payment" }).click();

  const stripe = page.frameLocator('iframe[title="Secure payment input frame"]');
  await stripe.getByLabel("Card number").fill(STRIPE_DECLINED_CARD.number, { timeout: 90_000 });
  await stripe.getByLabel(/Expiration/i).fill(STRIPE_DECLINED_CARD.expiry);
  await stripe.getByRole("textbox", { name: "Security code" }).fill(STRIPE_DECLINED_CARD.cvc);
  const zip = stripe.getByLabel("ZIP code");
  if (await zip.count()) await zip.fill(STRIPE_DECLINED_CARD.zip);

  await page.getByRole("button", { name: /COMPLETE PURCHASE/i }).click();

  // The decline surfaces somewhere the shopper can see -- either the widget's own message
  // inside the iframe, or this app's error line from onError.
  const ownError = page.getByRole("alert");
  const widgetError = stripe.getByText(/declined|error|try again/i).first();
  await expect
    .poll(async () => (await ownError.count()) > 0 || (await widgetError.count()) > 0, {
      timeout: 90_000,
      message: "neither the app nor the widget reported the decline",
    })
    .toBe(true);

  // And crucially: no navigation to a confirmation page.
  await page.waitForTimeout(3_000);
  expect(page.url()).not.toMatch(/\/checkout\/confirmation\//);
});
