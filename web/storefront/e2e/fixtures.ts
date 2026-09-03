/** A card Stripe always declines in test mode, with a generic decline. */
export const STRIPE_DECLINED_CARD = {
  number: "4000000000000002",
  expiry: "12/34",
  cvc: "123",
  zip: "27601",
};

/** A card Stripe always approves in test mode. */
export const STRIPE_TEST_CARD = {
  number: "4242424242424242",
  expiry: "12/34",
  cvc: "123",
  zip: "27601",
};

export const ADDRESS = {
  firstName: "Ada",
  lastName: "Tester",
  street: "123 Main St",
  city: "Raleigh",
  state: "North Carolina",
  postalCode: "27601",
  country: "US",
};

export function uniqueEmail(): string {
  return `pw-${crypto.randomUUID()}@example.com`;
}

/** A product with no options, so the cart takes it without a variant choice. */
export const KNOWN_PRODUCT = "Chianti Wine Glass";
