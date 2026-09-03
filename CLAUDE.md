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
  the option names the catalogue's families actually vary on.

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
```

## What is left

- `ct_merchant/`: the `MerchantBackend`, its config and the approval surface.
- The web apps: a storefront and a merchant portal from `examples/retail/*-web` over a copy
  of `examples/web-shared/`.
- Wire the real principal into `POST /api/session` (marked TODO in `service/main.py`).
- Set `REDIS_URL` before running more than one worker.
