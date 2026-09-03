# commercetools-anthropic-agents

A shopping agent and a merchant agent on commercetools, built on the packages from
`anthropics/commerce-agents` and pinned to one reviewed commit of it. The reference clone
at `~/dev/commerce-agents` is read-only source material, never a dependency path.

## Where the platform truth lives

`~/dev/commercetools-es-workspace` is the source of record for how this commercetools
project actually behaves: `.claude/skill-overrides/` for platform behaviour,
`.claude/mcp-feedback/` for tool-layer defects, `docs/shopping-agent-architecture.md` for
the previous agent build. Read the relevant override before trusting a skill or a doc.

Same discipline applies here. A learning that comes out of working in this repo is captured
before the task is done, not after: platform behaviour into the es-workspace override files
where it generalizes, and anything specific to this project into this file.

## Commerce agent decision record

### Both agents

- **Reference**: `anthropics/commerce-agents` at `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf`,
  pinned in `requirements.txt` as `git+...#subdirectory=<pkg>`. Never an editable path.
- **Path**: the Messages API runtime. Tools execute **in this process**, which is the
  reason this project exists rather than extending the previous build: with the tool loop
  inside the model provider's infrastructure, a cart write has already happened by the time
  any of our code can check it was scoped to the right shopper, and the previous build
  could only block the reply after the fact. Here the provenance gate, the quantity caps
  and fencing all run before the commercetools call.
- **Model access**: the Anthropic API directly. Use an **org** key; the previous build ran
  on a personal one and flagged it.
- **Identity**: commercetools OAuth2 `client_credentials`, one API client held by the
  process. The principal is bound at session start and read off the session record
  afterwards; no route and no tool argument carries a customer id (`tests/test_service.py`
  asserts this). Store fencing, if wanted, needs `:{storeKey}` in the API client's scope
  explicitly -- a project-scoped client can still call `/in-store/...` without being
  restricted to that store.
- **Sessions**: `RedisSessionStore` (`service/redis_sessions.py`) over the reference's six
  storage methods, with the version compare-and-set as a Lua script. Falls back to the
  in-process store without `REDIS_URL`, which is one worker only.
- **Transports**: REST for catalog reads, GraphQL with explicit field selection for cart
  and order reads. Measured on this project: REST Product Search is 144,902 bytes for five
  products against 34,786 through GraphQL, and a REST cart on a wide Product Type runs to
  103 KB against 2.6 KB. The fenced-result cap is 12,000 characters.
- **Reference-data cache**: Product Types, policy Custom Objects and shipping methods are
  read once per process (`ct_common/cache.py`). Re-fetching reference data per request is
  the top finding in commercetools Expert Services' own account reviews.

### Shopping agent

- **Module**: `ct_shopping/` -- `CommercetoolsStorefront`, config, executor subclass, a copy
  of the reference's five flows, tests. All five flows indexed.
- **Backend mapping**: search and details over `productProjectionSearch` and `product`
  (GraphQL); cart over the Carts API; orders and customer over GraphQL; policies over
  Custom Objects in the `policy-content` container (commercetools has no policy system);
  shipping over `/shipping-methods`.
- **Catalog shape, from the live data**: 85 of 100 sampled products have seven or more
  variants that are **identical on every attribute** -- duplicates, not choices -- so they
  map to plain products under the master variant. Options are derived from the attributes
  that actually differ across variants, not from `attributeConstraint`: on this project
  `CombinationUnique` appears on one Product Type only, while the main catalogue's `color`,
  `finish` and `size` are all `None`. Duplicate option combinations collapse to the
  cheapest in-stock variant.
- **Ids**: a family is the product id; a purchasable record is `{productId}#{variantId}`,
  because a commercetools variant has no global id of its own. A commercetools id never
  contains `#`, so the two namespaces cannot collide.
- **Search filters**: `filters.attributes` is applied in the backend **after** the
  platform's text match. A Product Search filter on an attribute defined with
  `isSearchable: false` returns zero matches with no error, and `color` and `finish` -- the
  two a shopper is most likely to name -- are both unsearchable here.
- **Price selection is mandatory**: without `priceCurrency`, `priceCountry` and
  `localeProjection`, a search result carries no name, image or price at all.
- **Stock**: a missing `availability` means no InventoryEntry, which means inventory is not
  tracked -- not out of stock. Only 10 of 100 products carry availability past the master
  variant, so reading absence as zero would empty the catalogue.
- **Checkout**: `checkout_handoff` returns `STOREFRONT_CHECKOUT_URL`. The storefront owns
  checkout and drives the real Checkout Sessions API and the payment connector; nothing
  here places an order or takes payment.
- **Domain errors mapped in the executor**: a frozen cart (a checkout still open) and a
  guest reaching a customer-only read. Both are ordinary states, not outages.
- **`domain_search_notes`** names only `size`, `color`, `finish` and `diameter-in-inches`,
  the option names the catalogue's families actually vary on, and tells the model to name a
  specific item rather than a category word.
- **Sorting happens over the relevance head, not the whole match set.** commercetools text
  search is loose -- "wine glass" matches 22 products here, candles and bowls among them --
  so handing `sorts: ["price asc"]` to the platform answers "cheapest wine glass" with a
  $1.99 bottle opener. The fetch window is deliberately small (`limit * 2`, capped at 16)
  and the sort runs locally within it. A **price range is the exception** and is pushed down
  as a platform filter, where it applies across the catalogue.
- **Prices are the discounted ones.** 62 of 159 products carry an applied Product Discount,
  so `price.discounted.value` wins over `price.value`; reading the list price misquotes more
  than a third of the catalogue. `labels` carries `sale` and `new`, derived from the applied
  discount and the `new-arrival` attribute.
- **Names are read from `nameAllLocales`, not `name(locale:)`.** Asked for one locale,
  GraphQL returns null when a product has no value for exactly that key, and two products
  here are named under `en` rather than `en-US` -- they rendered with no title at all.

### Checkout and payment

Real commercetools Checkout in `PaymentOnly` mode, over the already-deployed
`stripe-payment-connector`. The agent never touches any of it: its `checkout` tool renders
the cart and returns a handoff URL, and `service/checkout.py` is the host's own flow, driven
by the customer in the UI.

- **Checkout Application** `demo-commercetools-checkout-taxes` (`PaymentOnly`, Active), with
  its **Payment Integration** `Credit Card (Stripe)` at `componentType: DropIn` /
  `type: embedded` -- the only combination this connector's enabler accepts, and
  `componentType` cannot be changed after creation.
- **Sessions API** lives on its own host (`session.{region}.commercetools.com`) and needs a
  token with `manage_sessions`. That is a **separate grant from `manage_project`**, and an
  API client's scopes cannot be edited after creation, so this project runs on its own
  client (`commerce-agent-service`) created with both.
- **`allowedOrigins`** on the Application had to include this storefront's origin
  (`http://localhost:3005`, added with `addAllowedOrigin`); without it the widget will not
  load.
- **Sequence, in the order the platform requires:** address on the cart (order creation
  fails outright with `Shipping address is not set.`), then a shipping method, then the
  session -- created as late as possible, since sessions expire -- then the widget. Checkout
  creates the Payment and the Order itself; there is no place-order call here.
- **A Frozen cart is rejected outright** (`CartInvalidStateError`), so the cart is unfrozen
  first if a previous attempt left it that way. `unfreezeCart` is not idempotent, so the
  state is checked before the call rather than treating it as a no-op.
- **`paymentFlow` does not navigate on success.** `skipPaymentSuccessPage` and
  `skipPaymentErrorPage` default to true in `PaymentOnly` mode, so without the
  `checkout_completed` handler the card is charged and the order created while the UI shows
  nothing at all. `app/checkout/PaymentStep.tsx` navigates from that message.
- **`order.paymentState` is unset even after a successful charge** -- verified live on an
  order carrying both an Authorization Success and a Charge Success for the full gross
  amount. `payment_state_for_order` derives it from the Payment's transactions instead;
  reporting the raw field shows a paid order as unpaid.
- **The connector's processor scales to zero** and Checkout's backend calls it a few seconds
  after the session is created, so it is warmed (fire and forget) when a session is made. A
  cold processor presents as a generic "Payment failed".
- **The confirmation is scoped to the session's own principal.** Reading an order off a URL
  id with no ownership check is an IDOR; `e2e/storefront.spec.ts` asserts a foreign order id
  is refused.
- The Application's `paymentReturnUrl` still points at the other storefront's origin. That
  only matters for redirect-based methods that leave the page; inline card payment does not
  use it. Left alone rather than repointed, since the Application is shared.

Verified live end to end, four consecutive runs: a chat turn fills the cart, the checkout
route takes a real Stripe test card, and each run produced a commercetools Order with a
`Charge`/`Success` transaction matched to a Stripe PaymentIntent with `status: succeeded`
and `amount_received: 3886` in test mode.

### Merchant agent

- **Module**: `ct_merchant/`. Four flows indexed; `marketing-campaigns` is parked under
  `skills/_staged/` because commercetools has no campaign system.
- **Analysis**: `execute_analysis_query` over the read-only warehouse view that order
  events reach through Subscriptions -> Pub/Sub -> BigQuery, gated on
  `CT_ANALYSIS_BQ_DATASET`. Traffic and conversion return `None` with a note: that pipeline
  carries order events, not sessions. Platform Insights `ct_*` is API observability, not
  commerce metrics.
- **Approval**: `require_host_approval=True`. Every write is a staged change the host
  applies; staged changes become commercetools update actions only on approval.
- **Status**: not yet implemented. See "What is left".

## Project gotchas

- **An API Extension with an unguarded `custom(fields(...))` trigger condition breaks every
  cart write in the project.** Such a predicate cannot be evaluated against a cart with no
  custom object, so cart Update fails with `ExtensionPredicateEvaluationFailed` for this
  agent and for anything else, the storefront included. Fourteen leftover `ext-lab-*`
  extensions were doing this and have been removed. `scripts/check_extensions.py` reports
  the condition; `CT_CART_CUSTOM_MARKER=1` is the workaround if one ever comes back.
- **`/shipping-methods` and `/shipping-methods/matching-cart` answer with a bare JSON
  array**, not a paged `{results}` object. `CTClient.get_list` handles both.
- **`matching-cart` needs a shipping address on the cart** and fails without one. The agent
  never collects an address, so the country's methods are the answer before checkout.
- **A commercetools predicate comparing `id` to a non-UUID is rejected** as a malformed
  parameter rather than matching nothing, so `id` only enters a predicate for a real UUID.
- **`kmb-core-commerce-lab` is a shared lab.** It accumulates other prototypes' data --
  `perf-cartsize-*` Product Types, `KMB `-prefixed attribute values, `ext-lab-*` resources.
  Inventory what is there before repurposing anything, and expect catalogue oddities to be
  data, not bugs.
- **Never pipe a commercetools JSON response through `echo ... | jq`** -- control characters
  corrupt the parse. Write with `curl -o` and run `jq` against the file.
- Order totals are `taxedPrice.totalGross`. A cart subtotal is summed from line items;
  `cart.totalPrice` silently includes shipping once a shipping method is set.

## Verify

```bash
ruff check . && ruff format --check . && pytest      # no network
python scripts/check_extensions.py                   # cart writes are unblocked
python scripts/probe_backend.py                      # every backend method, live
python scripts/smoke_chat.py                         # one live conversation, needs both keys

cd web && npm run build                              # the storefront type-checks and builds
cd web/storefront && npx playwright test             # the browser suite; checkout.spec.ts
                                                     # takes a real Stripe test card and
                                                     # creates a real Order on each run
```

## The storefront

`web/` is an npm workspace: `web-shared` (copied from the reference) and `storefront`, a
Next.js app on port 3005. `lib/session.ts` persists the session id in `localStorage`, which
the reference's own `useSession` does not do -- it starts a fresh session per mount, and a
new session means a new anonymous id and therefore a different cart, so `/checkout` would
arrive empty. The catalogue routes are public and write no provenance; the only
add-to-cart path in the app runs off the agent's own product cards, through the same
executor as the agent's tool call, so the provenance gate and quantity caps hold for a
click exactly as they do for the model.

`e2e/` holds the Playwright suite. The Stripe card fields are in a cross-origin iframe:
page-context JS cannot reach them, but `frameLocator` drives them over CDP. Target the
visible label text (`Card number`, `Security code`), not the placeholders, which are
unrelated example content.

## What is left

- `ct_merchant/`: the `MerchantBackend`, its config and the approval surface.
- The merchant portal web app, from `examples/retail/merchant-web`.
- Wire the real principal into `POST /api/session` (marked TODO in `service/main.py`).
- Set `REDIS_URL` before running more than one worker.
- Repoint `NEXT_PUBLIC_API_URL` and the Checkout Application origin for any deployed host.
