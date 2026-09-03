# Screenshots — Claude Commerce Agents on commercetools

Captured 2026-09-03 from the running local stack (API :8000, shop :3005, portal :3105)
against the live `kmb-core-commerce-lab` commercetools project. Full-resolution PNGs here;
`compressed/` holds 1100px JPEGs and `small/` 760px JPEGs.

| File | What it shows | Blog marker |
|---|---|---|
| `01-storefront-home.png` | The shop on load: guest session, live catalogue, discounted prices | 1 |
| `02-search-results.png` | One turn: four real rugs under $150 as product cards, with follow-up chips | 2 |
| `03-product-detail.png` | A card expanded, showing the product's real commercetools description | 3 |
| `04-checkout-handoff-card.png` | The `checkout` tool's card: itemised cart, "Not charged", handoff link | 4 |
| `05-checkout-address.png` | Checkout step 1 — the address the platform requires before an order exists | 5 |
| `06-checkout-delivery.png` | Checkout step 2 — the project's five real shipping methods with `freeAbove` | 6 |
| `07-checkout-stripe-payment.png` | Checkout step 3 — the real Stripe Payment Element, total $99.98 | 7 |
| `07b-stripe-card-filled.png` | The test card entered in Stripe's cross-origin iframe | 8 |
| `08-order-confirmed.png` | A real paid order: `paid`, $38.86 charged, real commercetools order id | 9 |
| `09-portal-dashboard.png` | The back office: real sales/orders/AOV, Conversion as an em dash, derived alerts | 10 |
| `10-change-preview-approval.png` | A staged restock: the agent's reasoning, the other-channel warning, Approve/Dismiss | 11 |
