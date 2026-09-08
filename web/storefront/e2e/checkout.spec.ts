import { expect, test } from "@playwright/test";
import { ADDRESS, KNOWN_PRODUCT, STRIPE_TEST_CARD, uniqueEmail } from "./fixtures";

/**
 * The full happy path against the real stack: the agent fills the cart through a real
 * model turn, the real commercetools Checkout (PaymentOnly, Sessions API + Browser SDK
 * paymentFlow) runs the payment step, and the deployed Stripe connector takes a real test
 * card. Nothing here is mocked, and each run creates a real Order.
 *
 * The card fields live in a cross-origin iframe titled "Secure payment input frame".
 * Page-context JS cannot reach into it, but Playwright's frameLocator drives it over CDP
 * directly. The visible label text is what to target ("Card number", "Security code") --
 * the placeholders are unrelated example content like "1234 1234 1234 1234".
 */
test("a shopper can go from a chat request to a paid order", async ({ page }) => {
  const messages: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") messages.push(message.text());
  });

  await page.goto("/");
  // The shop greets the visitor by name; "Guest" was the pre-rebrand placeholder.
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Good morning");

  // 1. The agent puts the item in the cart. This is the only add-to-cart path in the app:
  //    the tile's own button asks the assistant rather than writing the cart directly.
  await page.getByRole("textbox", { name: /Message/ }).fill(`Add the ${KNOWN_PRODUCT} to my cart.`);
  await page.getByRole("textbox", { name: /Message/ }).press("Enter");

  const cart = page.getByRole("complementary", { name: "Cart" });
  // exact: true -- the panel also renders "Subtotal · 1 item", which substring-matches.
  await expect(cart.getByText("1 item", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(cart.getByText(KNOWN_PRODUCT).first()).toBeVisible();

  // 2. Checkout is a route of the host app; the agent's card links to it and charges nothing.
  await page.goto("/checkout");
  await expect(page.getByRole("heading", { name: "Checkout" })).toBeVisible();
  await expect(page.getByText(KNOWN_PRODUCT).first()).toBeVisible();

  // 3. The address has to be on the cart before an order can be created at all.
  await page.getByRole("textbox", { name: "First name" }).fill(ADDRESS.firstName);
  await page.getByRole("textbox", { name: "Last name" }).fill(ADDRESS.lastName);
  await page.getByRole("textbox", { name: "Email" }).fill(uniqueEmail());
  await page.getByRole("textbox", { name: "Street address" }).fill(ADDRESS.street);
  await page.getByRole("textbox", { name: "City" }).fill(ADDRESS.city);
  await page.getByRole("textbox", { name: "State" }).fill(ADDRESS.state);
  await page.getByRole("textbox", { name: "Postal code" }).fill(ADDRESS.postalCode);
  await page.getByRole("button", { name: "Save address" }).click();

  // 4. A paid method, so the amount charged is the item plus shipping rather than the item
  //    alone -- a $0 total would not exercise the connector the same way.
  await expect(page.getByText("Delivery method")).toBeVisible({ timeout: 60_000 });
  const economy = page.getByRole("radio", { name: /Economy Ground Shipping/ });
  await economy.click();
  await expect(economy).toBeChecked();
  await page.getByRole("button", { name: "Continue to payment" }).click();

  // The summary switches from Subtotal to Total once shipping is applied.
  await expect(page.getByText("Total", { exact: true })).toBeVisible({ timeout: 60_000 });

  // 5. The real Stripe Payment Element, inside its cross-origin iframe.
  const stripe = page.frameLocator('iframe[title="Secure payment input frame"]');
  await stripe.getByLabel("Card number").fill(STRIPE_TEST_CARD.number, { timeout: 90_000 });
  await stripe.getByLabel(/Expiration/i).fill(STRIPE_TEST_CARD.expiry);
  // Exact match: a loose /CVC/i also matches a decorative icon labelled
  // "Credit or debit card CVC".
  await stripe.getByRole("textbox", { name: "Security code" }).fill(STRIPE_TEST_CARD.cvc);
  const zip = stripe.getByLabel("ZIP code");
  if (await zip.count()) await zip.fill(STRIPE_TEST_CARD.zip);

  await page.getByRole("button", { name: /COMPLETE PURCHASE/i }).click();

  // 6. paymentFlow does not navigate on its own: the host does it from checkout_completed.
  //    Reaching the confirmation route is therefore also the proof that handler fired.
  await page.waitForURL(/\/checkout\/confirmation\//, { timeout: 150_000 });
  await expect(page.getByRole("heading", { name: "Order confirmed" })).toBeVisible();
  await expect(page.getByTestId("order-id")).not.toBeEmpty();
  await expect(page.getByText(KNOWN_PRODUCT).first()).toBeVisible();

  const total = await page.getByTestId("order-total").textContent();
  const orderId = await page.getByTestId("order-id").textContent();
  const paymentState = await page.getByTestId("payment-state").textContent();
  console.log(`order ${orderId} total ${total} payment ${paymentState}`);

  // A real charge means a real total: $25.99 item + $9.99 shipping + tax from the
  // connector, so somewhere in the $30s and never $0.00.
  expect(total).toMatch(/\$3[0-9]\.\d\d/);
  // "paid" is derived from the Payment's own Charge transaction, because commercetools
  // leaves order.paymentState unset even after a card has really been charged.
  expect(paymentState).toBe("paid");
  expect(messages.filter((m) => !m.includes("favicon"))).toEqual([]);
});
