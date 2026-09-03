/**
 * The storefront's own copy, kept true to what this commercetools project actually
 * configures. The reference's values (free shipping over $49, a 3–5 business day standard
 * ETA) are not this project's: its shipping methods go free above $500, and commercetools
 * carries no delivery estimate at all, so none is claimed here.
 *
 * `data/policy-content.json` is the matching source for the passages the agent reads out
 * of the `policy-content` Custom Objects; keep the two consistent.
 */
export const STORE_POLICY = {
  returnsShort: "30-day returns",
  returnsLine:
    "Most items can be returned within 30 days of delivery for a refund to your original payment method.",
  freeShippingThreshold: 500,
  standardShippingEta: "quoted at checkout",
} as const;
