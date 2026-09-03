# commercetools-anthropic-agents

A shopping agent and a merchant agent for commercetools, built on
[`anthropics/commerce-agents`](https://github.com/anthropics/commerce-agents).

The agents reach commercetools through a backend this project owns, and their tools run in
this process. That is deliberate: the gates that matter — provenance on every cart write,
quantity caps, fencing of everything the platform returns — run *before* the commercetools
call, and the shopper's identity never enters the model's context at all.

The shopping half is complete and works end to end against a live project: a chat turn fills
the cart, and checkout takes a real card through commercetools Checkout and the deployed
Stripe connector.

## Run it

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # commercetools credentials + an org ANTHROPIC_API_KEY
uvicorn service.main:app --port 8000

cd web && npm install
cd storefront && npx next dev -p 3005
```

The API client needs **both** `manage_project` and `manage_sessions`; the Sessions API that
Checkout runs on is a separate grant, and commercetools cannot add a scope to an existing
API client. The storefront's origin must be in the Checkout Application's `allowedOrigins`.

### Routes

```
POST   /api/session                  start a session; a customer id makes it a member, absent makes it a guest
POST   /api/chat                     one turn, streamed as SSE agent events        (X-Session-Id)
GET    /api/products[/{id}]          the catalogue (public)
POST   /api/cart/add                 the card's Add button, through the agent's own executor
GET    /api/cart                     the session's cart                            (X-Session-Id)
GET    /api/orders                   the session's own orders                      (X-Session-Id)
GET/PATCH/DELETE /api/memory         what is remembered about this shopper         (X-Session-Id)
POST   /api/checkout/address         address onto the cart, and the matching shipping methods
POST   /api/checkout/shipping-method choose one
POST   /api/checkout/session         a real commercetools Checkout Session
GET    /api/checkout/order/{id}      the confirmation, scoped to this session's principal
POST   /api/reset                    drop the session
GET    /api/health                   project and store
```

## Layout

| Path | What it is |
|---|---|
| `ct_common/` | commercetools settings, the authenticated client, the reference cache, the Checkout Sessions client, the domain errors, the projection mappers |
| `ct_shopping/` | `CommercetoolsStorefront`, config, executor subclass, the five flows, tests |
| `ct_merchant/` | the merchant agent's flows; the backend is not written yet |
| `service/` | the FastAPI service, the checkout routes, the session store and its Redis subclass |
| `web/` | npm workspace: `web-shared` and the Next.js `storefront`, including the checkout and confirmation routes and the Playwright suite |
| `scripts/` | `probe_backend.py`, `smoke_chat.py`, `check_extensions.py`, `seed_policies.py`, `ensure_cart_type.py` |

`CLAUDE.md` carries the decision record: what each backend method maps onto, what the live
catalogue's data turned out to be, and the platform behaviour that shaped the code. Read it
before changing a backend method — several decisions there look arbitrary and are not.

## Verify

```bash
ruff check . && ruff format --check . && pytest      # no network
python scripts/check_extensions.py                   # cart writes are unblocked
python scripts/probe_backend.py                      # every backend method, live
python scripts/smoke_chat.py                         # one live conversation

cd web && npm run build
cd web/storefront && npx playwright test             # the browser suite
```

`e2e/checkout.spec.ts` takes a **real Stripe test card** and creates a **real commercetools
Order** on every run. It is not mocked, and it is not idempotent.
