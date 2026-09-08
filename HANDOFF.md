# Handoff — building commercetools skills for Anthropic's commerce agents

**Goal of the next phase:** turn what this build learned into commercetools skills that let an
implementer put Anthropic's `commerce-agents` on commercetools without rediscovering any of it.

This repo is the worked example. It runs against a live project (`kmb-core-commerce-lab`) and
takes a real Stripe payment. Read `CLAUDE.md` first — it is the decision record, and several
decisions in it look arbitrary and are not.

---

## 1. What exists

### This repo — the reference implementation

| Path | What it is | Skill relevance |
|---|---|---|
| `ct_common/client.py` | Authenticated CT client: token cache, REST + GraphQL, error mapping | **High** — the transport split is a real decision |
| `ct_common/graphql.py` | The six GraphQL documents, plus shape normalizers | **High** — copy-paste starting point |
| `ct_common/mapping.py` | Projection → agent records: options, prices, stock, locales, ids | **Highest** — this is where all the traps live |
| `ct_common/checkout.py` | Checkout Sessions client + processor warm-up | **High** |
| `ct_common/cache.py` | Reference-data TTL cache | Medium |
| `ct_common/errors.py` | The domain errors worth mapping | Medium |
| `ct_common/sync_client.py` | Sync CT client, for the sync store interfaces | Low (mechanical) |
| `ct_shopping/backend.py` | `StorefrontBackend` over commercetools | **Highest** |
| `ct_shopping/config.py` | Search notes, id patterns, switches | High |
| `ct_shopping/executor.py` | Domain-error mapping (frozen cart, guest) | Medium |
| `ct_shopping/session.py` | Identity: `customerId` vs `anonymousId` | **High** |
| `ct_merchant/backend.py` | `MerchantBackend` over commercetools | **Highest** |
| `ct_merchant/ledger.py` | Staged-change ledger on Custom Objects | High |
| `ct_merchant/config.py` | Switches for what CT does not have | High |
| `service/` | FastAPI host: chat SSE, checkout, merchant router, session stores | Medium |
| `web/storefront`, `web/portal` | Next.js apps from the reference's examples | Low |
| `scripts/` | Six live probes and utilities — see below | **High** as skill validation steps |

### Scripts — each one is a candidate skill validation step

| Script | What it proves |
|---|---|
| `check_extensions.py` | No API Extension is blocking cart writes project-wide |
| `probe_backend.py` | Every `StorefrontBackend` method against the live project |
| `probe_merchant.py` | Every `MerchantBackend` read, plus guardrail refusals |
| `smoke_chat.py` | One live conversation with cache-read figures |
| `seed_policies.py` | Seeds the policy Custom Objects CT has no system for |
| `ensure_cart_type.py` | The unguarded-extension workaround |
| `demo_reset.py` | Clears staged changes and sessions, nothing else |

### Documents

- `CLAUDE.md` — the decision record. **The primary input for skill content.**
- `README.md` — layout, endpoints, verification.
- `DEMO.md` — the six-minute in-person arc, with measured timings and failure modes.
- `LOOM.md` — three recorded clips with narration.
- `docs/screenshots/` — 11 screenshots + `MANIFEST.md`.
- `docs/slack-announcement.md` — the internal announcement.
- Blog post (Google Docs): *FLASH: Claude's Commerce Agents, Running on commercetools*.

### Contributions back to the ES workspace

Appended to `~/dev/commercetools-es-workspace`, **uncommitted** (that repo had pre-existing
uncommitted work; do not `git add -A` there):

- `.claude/skill-overrides/commercetools-platform.md` — **8 new entries**
- `.claude/skill-overrides/commercetools-checkout.md` — **4 new entries**
- `.claude/mcp-feedback/chrome-devtools.md` — **new file, 2 entries**

All dated 2026-09-03 and written to the workspace's own conventions: bolded correct
behaviour, then the evidence, generalized so a reader without this repo can use them.
**These 14 entries are the highest-density skill input in the whole exercise.**

---

## 2. Decisions that had to be made

Each of these is a fork where the obvious choice is wrong or arguable. A skill should present
them as decisions with a recommendation, not as facts.

### Architecture

| Decision | Chosen | Why it matters |
|---|---|---|
| Tools in-process vs a CT MCP server | **In-process** | With the tool loop in the model provider's infra, a cart write has already happened before your code can check it was scoped to the right shopper. You can block the reply, not the write. |
| Which runtime | Messages API | Agent SDK and Managed Agents also work; this one keeps the gates in our process |
| Package sourcing | Pinned git refs | Never editable paths. `pyproject.toml` needed for uv-based builds (see §6) |
| Model per role | Sonnet for both | Reference defaults merchant to Opus; the merchant workload is reads and arithmetic |

### Identity and sessions

| Decision | Chosen | Why |
|---|---|---|
| Auth to CT | Service credential (`client_credentials`) | Principal bound at session start, read off the record; no route carries a customer id |
| Guest identity | `anonymousId` | A Cart's `customerId` only accepts a registered Customer; get it wrong and **neither** field is set |
| Store scoping | Off (no `CTP_STORE_KEY`) | If wanted, needs `:{storeKey}` in the API client scope explicitly, and `store` is creation-time only on a Cart |
| Session store | CT Custom Objects (Redis subclass also written) | Custom Objects give platform-enforced compare-and-set and ≥1 MB values; no new vendor |

### Catalogue modelling

| Decision | Chosen | Why the obvious choice fails |
|---|---|---|
| What is a "variant option" | Attributes that **differ across variants** | `attributeConstraint: CombinationUnique` appears on 1 of 8 Product Types here; the main catalogue's `color`/`finish`/`size` are all `None` |
| Duplicate variants | Collapse to cheapest in-stock | 85 of 100 products have 7+ byte-identical variants |
| Which price to quote | `discounted.value` for shoppers, `value` for merchants | 62 of 159 products carry a discount; conflating the two makes a repricing cap check disagree with the screen |
| Which price to **write** | The one CT's own price selection resolves | A variant had 6 prices, 2 USD; CT resolves USD/US to the *unscoped* one, not the country-scoped one |
| Product names | `nameAllLocales` + client-side fallback | `name(locale:)` returns null for a product authored under `en` not `en-US` |
| Where filters run | Attributes locally, price range at the platform | An `isSearchable: false` attribute filter returns zero matches **with no error** |
| Sorting | Locally, over a tight relevance head | CT text search is loose; platform `price asc` answers "cheapest wine glass" with a $1.99 bottle opener |
| Stock | The channel-less inventory entry | One entry per supply channel; judging one flags well-stocked items, judging the total hides real shortages |
| Ids | `{productId}#{variantId}` | A CT variant has no global id; `#` never appears in a CT id |

### Checkout

| Decision | Chosen |
|---|---|
| Who owns payment | commercetools Checkout, PaymentOnly, on the deployed Stripe connector |
| Agent's role | `checkout` renders the cart and returns a handoff URL. No agent tool can pay |
| Payment state | Derived from the Payment's transactions — `order.paymentState` is unset even after a real charge |
| Cart state | Unfrozen first if needed; `unfreezeCart` is not idempotent |

### Merchant writes

| Decision | Chosen |
|---|---|
| Approval | `require_host_approval=True`; the mark is set for one click and cleared after |
| Check order | Staged-status and guardrails **before** the platform write |
| Campaigns | `enable_campaigns=False` — CT has no campaign object |
| Analysis delegate | Off until a BigQuery view exists |
| Missing figures | `None` + a note, never a zero |

---

## 3. Backend mapping

### `StorefrontBackend` → commercetools

| Method | commercetools | Notes for the skill |
|---|---|---|
| `search_products` | GraphQL `productProjectionSearch` | Price selection + locale mandatory or results carry no name/price. Over-fetch `limit*2` capped 16 |
| `get_product_details` | GraphQL `product` (masterData) | Family = product id; variant = `{productId}#{variantId}` |
| `get_cart` | GraphQL `carts(where:)` by owner predicate | Found by `anonymousId`/`customerId`, not stored client-side |
| `add_to_cart` | `POST /carts` then `addLineItem` | Cart draft sets currency/country/locale/store — all creation-time |
| `update_cart_item` | `changeLineItemQuantity` | Must map product id → `lineItemId` |
| `remove_from_cart` | `removeLineItem` | Same mapping |
| `get_preferences` | `GET /customers/{id}` | Guest returns a guest profile |
| `get_orders` / `get_order` | GraphQL `orders(where:)` | Scoped to the principal. `taxedPrice.totalGross`, not `totalPrice`. `id` only in a predicate if UUID |
| `search_policies` | Custom Objects, container `policy-content` | **CT has no policy system.** Cached |
| `get_fulfillment_options` | `/shipping-methods/matching-cart`, falling back to `/shipping-methods?country=` | Bare JSON array. matching-cart needs an address |
| `checkout_handoff` | `STOREFRONT_CHECKOUT_URL` | Never reaches the model |
| `get_disclosure` | not implemented | `enable_disclosures=False` |

Host-only additions: `browse_products`, `prepare_for_checkout`, `checkout_shipping_methods`,
`set_shipping_method`, `cart_for_checkout`, `order_for_session`, `payment_state_for_order`.

### `MerchantBackend` → commercetools

| Method | commercetools | Notes |
|---|---|---|
| `get_business_snapshot` | Orders, two windows in parallel | traffic/conversion `None`; AOV derived |
| `query_metrics` | Orders bucketed by day | Unsupported metric → empty + note |
| `get_campaign_performance` | — | Raises; `enable_campaigns=False` |
| `search_listings` | `/product-projections/search?staged=true` | REST returns `name` as a LocalizedString |
| `get_listing` | `GET /products/{id}` staged | Variants + missing-attribute derivation |
| `get_inventory_alerts` | `/inventory` + Orders | Derived. Aggregate per sku, judge the sellable channel |
| `get_order_issues` | Orders `paymentState`/`shipmentState` | Derived; only computable kinds |
| `get_pricing_context` | `/product-projections/search` with price selection | `unit_cost`/`margin` `None`; `min_price_basis="policy"` |
| `stage_*` | nothing — proposals only | Guardrails at staging |
| `apply_change` | `changeName`/`setDescription`, `changePrice`, `addQuantity`, `publish`/`unpublish` | The only platform write |
| `execute_analysis_query` | BigQuery view (not wired) | Gated on `CT_ANALYSIS_BQ_DATASET` |
| `get_merchant_context` | — | Carries the `limitations` list |

---

## 4. Endpoints — 29 total

### Shopping and catalogue
```
POST   /api/session                     start a session (customer id → member, absent → guest)
POST   /api/chat                        one turn, SSE agent events            (X-Session-Id)
GET    /api/products                    the shelf (public, no session)
GET    /api/products/{product_id}       one product (public)
POST   /api/cart/add                    card's Add button, via the agent's own executor
GET    /api/cart                        the session's cart                    (X-Session-Id)
GET    /api/orders                      the session's own orders              (X-Session-Id)
GET|PATCH|DELETE /api/memory            what is remembered                    (X-Session-Id)
POST   /api/reset                       drop the session
GET    /api/health
```

### Checkout — host flow, no agent tool reaches these
```
POST   /api/checkout/address            address onto the cart, returns matching methods
GET    /api/checkout/shipping-methods   methods for this cart
POST   /api/checkout/shipping-method    choose one; returns the new total
POST   /api/checkout/session            a real commercetools Checkout Session
GET    /api/checkout/order/{order_id}   confirmation, scoped to this session's principal
```

### Merchant
```
POST   /api/merchant/session            operator is server-side, never in a request
POST   /api/merchant/chat               one turn, SSE                         (X-Session-Id)
GET    /api/merchant/overview           the portal's whole home-page data plane
GET    /api/merchant/alerts             derived inventory + order issues
GET    /api/merchant/listings           search
GET    /api/merchant/listings/{id}      listing + pricing context
POST   /api/merchant/changes/{id}/apply     the Approve button, through the same gate
POST   /api/merchant/changes/{id}/discard   the Dismiss button
GET|DELETE /api/merchant/memory
POST   /api/merchant/reset
GET    /api/merchant/health
```

Event types on both chat streams: `text_delta`, `tool_call`, `tool_result`, `ui`,
`ui_partial`, `cart_update` (shop) / `change_update` (merchant), `progress`, `turn_complete`,
`error`. The type is on the SSE `event:` line; `data:` carries only the payload.

---

## 5. Project setup this required

Reusable checklist for a skill:

1. **API client with `manage_project` AND `manage_sessions`** — separate grants, and scopes
   cannot be edited after creation. Also `view_payments`/`manage_payments` if the app reads
   or writes Payments under its own client.
2. **Checkout Application** in `PaymentOnly`, Active, with the storefront origin in
   `allowedOrigins` (`addAllowedOrigin` is additive).
3. **Payment Integration** for the Stripe connector: `componentType: DropIn`, `type: embedded`.
   `componentType` cannot be changed — wrong value means delete and recreate.
4. **Policy content** — CT has no system for it. Custom Objects, seeded and cached.
5. **No API Extension with an unguarded `custom(fields(...))` condition** — one breaks every
   cart write in the project, for every client.
6. **Tax Category on every sellable product** — absence blocks cart creation, and it surfaces
   at cart time, not product-save time.

---

## 6. Traps that cost the most time

Ranked by how expensive they were, which is roughly how prominent they should be in a skill.

1. **Price writes targeting the wrong price.** Read the resolved `price.id`; don't model selection.
2. **`apply_change` writing before checking the change was still staged.** Applying twice wrote twice.
3. **Inventory judged per supply channel.** Seven false "sold out" alerts on well-stocked products.
4. **Fourteen `ext-lab-*` extensions blocking all cart writes.** Project-wide blast radius.
5. **`order.paymentState` unset after a real charge.** A paid order reads as unpaid.
6. **`paymentFlow` not navigating on success.** Card charged, order created, UI shows nothing.
7. **`isSearchable: false`** → zero matches, no error, reads as "we don't sell those".
8. **List vs discounted price** across the two agents.
9. **`name(locale:)` returning null** → untitled products.
10. **uv dropping `#subdirectory=`** on Vercel → needs a `pyproject.toml` with `[tool.uv.sources]`.
11. **Custom Objects only bump their version when the value changes** → fold a counter in, or
    the compare-and-set silently stops working and a concurrent turn is lost.

---

## 7. Suggested skill shape

Not a single skill. What the material actually supports:

- **`commercetools-agent-backend`** — the core: mapping `StorefrontBackend`/`MerchantBackend`
  onto CT, with the catalogue-modelling decisions from §2 and the traps from §6. The bulk of it.
- **`commercetools-agent-checkout`** — the PaymentOnly sequence, the scope wall, the Payment
  Integration values, the derived payment state. Mostly already in the checkout override.
- **`commercetools-agent-hosting`** — sessions and the staged-change ledger without instance
  affinity, the identity model, what a serverless deployment breaks.

Each should carry a **validation step** (the `scripts/probe_*.py` pattern) and a **project
readiness check** (§5), because most of the failures here were data or configuration, not code.

**Generalizable vs project-specific.** Everything in §2, §3, §5 and §6 generalizes. These do
not, and must be marked as such: the `KMB` attribute prefix, the 85%-duplicate-variants figure,
the specific Checkout Application key, and `kmb-core-commerce-lab` being a shared lab whose
data accumulates across prototypes.

---

## 8. State at handoff

- 51 offline tests, 11 browser tests (7 storefront, 4 portal), ruff clean.
- Verified live: every backend method, a real paid order end to end, a real merchant price
  change and inventory move applied through the approval gate.
- Personal `ANTHROPIC_API_KEY` in `.env` — **replace with an org key**.
- Not done: real authentication at session start, spend cap on the model key, and the Vercel
  deployment (API deploys and builds; it sits behind Vercel Authentication and the account is
  on Hobby, so shared-password protection is unavailable).
- Additive writes made to the shared lab: `policy-content` Custom Objects, an
  `agent-cart-marker` Type (now unused), the `commerce-agent-service` API client,
  `http://localhost:3005` on the Checkout Application's origins, plus test orders and two
  applied merchant changes.
