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

- **Module**: `ct_merchant/` -- `CommercetoolsMerchant`, config, executor subclass, the four
  indexed flows, tests. Mounted at `/api/merchant` in the same process as the storefront,
  so an approved change shows in the shop at once.
- **Reads**: sales, orders and AOV derived from Orders; listings from staged product data;
  inventory from Inventory Entries; order issues from payment and shipment state; pricing
  from the price commercetools itself resolves.
- **Writes** are staged proposals. Only `apply_change` touches the platform, translating a
  change into update actions: `changeName`/`setDescription`, `changePrice`, `addQuantity`,
  `publish`/`unpublish`. **Both the staged check and the guardrails run before the write** --
  they used to run after it, which meant applying an already-applied change wrote a second
  time and only then raised, charging a price move or a restock twice.
- **`require_host_approval=True`.** `apply_change` succeeds only for a change id the host
  marked approved; a click sets the mark immediately before the executor runs and clears it
  immediately after, whatever the outcome, so a later chat turn cannot spend a leftover.
- **The host's overview read records what it showed as provenance.** The change queue is
  process-wide (the reference's ledger is in-memory for the whole process) while
  `seen_changes` is per session, so without this the portal rendered Approve and Dismiss
  for a change the gate then refused. The gate exists to stop the *model* naming a change
  id it never saw, not to stop the store's own queue being worked; approval is still
  required to apply.
- **Which price a write targets is decided by the platform, not by us.** A variant here
  carries six prices across three currencies, two of them USD, and a raw product read gives
  no indication of which one selection picks. Reads and writes both go through
  `/product-projections/search` with price selection, whose resolved `price` carries its own
  `id`. A "sensible" heuristic preferring the `country: "US"` price disagreed with the
  platform, which resolves USD/US to the **unscoped** price on this data -- and for a write
  that means repricing a price the shop never displays.
- **The merchant reads the list price; the storefront quotes the discounted one.** Showing
  an operator the discounted figure had search saying $23.52 and the pricing context $27.67
  for the same listing, which is how a 5% increase gets recorded against a base nobody saw.
  `money(..., effective=False)` is the merchant's reading.
- **Stock means what the shop can sell.** A sku carries one Inventory Entry per supply
  channel -- up to five here, with very different quantities -- and this storefront creates
  carts with no supply channel, so the channel-less entry is the sellable figure and the
  same one the shopping agent reads. Judging a single low entry flagged seven well-stocked
  products (95 to 155 units in total) as sold out; judging the total instead hid five
  genuine shop-level shortages. A restock proposal names the units sitting in other
  channels in `guardrail_notes`, because reordering when the stock is a channel away is the
  wrong call. Negative quantities are reported as they stand: oversold is real state here.
- **What commercetools cannot supply is `None` with a note, never a zero**: traffic and
  conversion (no sessions), unit cost and therefore margin (so the price floor is a store
  rule, `min_price_basis="policy"`), and campaigns, which have no object at all --
  `enable_campaigns` is off and `marketing-campaigns` stays parked under `skills/_staged/`.
  All three are in the `limitations` list on the merchant context, and the portal's
  Conversion tile renders an em dash rather than 0%.
- **The analysis delegate** stays off until `CT_ANALYSIS_BQ_DATASET` names the read-only
  warehouse view that order events reach through Subscriptions -> Pub/Sub -> BigQuery.

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
cd web/portal && npx playwright test                 # the portal suite
python scripts/probe_merchant.py                     # every merchant read, live
```

## The web apps

`web/` is an npm workspace with three members: `web-shared` (copied from the reference),
`storefront` (port 3005) and `portal` (port 3105).

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

## The portal

`web/portal/`, from `examples/retail/merchant-web`. Its response shapes are the reference's
(`OverviewResponse`, `AlertsResponse`, `ListingDetailResponse`), so the service matches them
rather than the app being rewritten. The assistant panel does not offer campaigns: the tools
are not registered, and advertising them would only produce a refusal.

Two things to expect when testing it. The change queue is **in-memory and process-wide**, so
staged changes accumulate across runs and clear on restart -- a test that assumes it is the
only pending change will fail. And a test should assert the audit trail ("Dismissed by
<operator>") rather than a card's buttons disappearing, which is a rendering detail of the
vendored component.

## What is left

- Wire the real principal into `POST /api/session` (marked TODO in `service/main.py`), and
  a real operator identity into the merchant router (currently `MERCHANT_OPERATOR`).
- Put the change ledger behind a store, so staged changes survive a restart and are shared
  between workers. It is in-process today.
- Set `REDIS_URL` before running more than one worker.
- Repoint `NEXT_PUBLIC_API_URL` and the Checkout Application origin for any deployed host.
