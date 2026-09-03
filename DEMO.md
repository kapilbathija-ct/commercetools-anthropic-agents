# Demo guide — Hi Kapil

Two agents on a live commercetools project, with a real Stripe payment. About six minutes.

## Before the room

```bash
# 1. Clean state: clears staged changes and stored sessions. Nothing else is touched.
python scripts/demo_reset.py --confirm

# 2. Three processes. Production mode, so no Next.js dev overlay in your screenshare.
uvicorn service.main:app --port 8000                      # API
cd web/storefront && npx next start -p 3005               # the shop
cd web/portal    && npx next start -p 3105                # the back office

# 3. Confirm cart writes are not blocked (an unguarded API Extension breaks every one).
python scripts/check_extensions.py     # expect: "cart writes are clean"
```

Open four tabs: the shop (3005), the portal (3105), the Stripe dashboard (test mode), and
the commercetools Merchant Center. The last two are for the proof moment in step 3.

**Warm it up.** Send one throwaway turn in each app before you present. The first turn of a
process pays for the token fetch and the product-type read.

## The arc

### 1. Frame it — 30 seconds

> "Anthropic open-sourced their commerce agents last week. Every demo of it runs on
> fixtures — a mock catalogue, a checkout that charges nothing. I pointed it at our own lab
> project: 159 real products, 738 orders, real discounts, our deployed Stripe connector."

### 2. The shop — one turn, about 10 seconds

Click the starter **"A lounge chair for a small living room, under $250"**.

While it runs, say what to watch for:

> "Those prices are what a customer actually pays. 62 of our 159 products carry an applied
> product discount — read the list price instead and you misquote a third of the catalogue."

Then add one to the cart from a card.

### 3. Checkout — the moment that lands

Click **Check out** in the cart panel. The assistant renders the checkout card: itemised,
**"Not charged"**, with a handoff link.

> "The agent's checkout tool renders the cart and stops. No agent tool in this build can
> take a payment."

Follow the link. Address → delivery (**these are our five real shipping methods, with the
free-above thresholds**) → payment.

The payment step is **real commercetools Checkout on our deployed Stripe connector**. Use:

```
card 4242 4242 4242 4242    expiry 12/34    CVC 123    ZIP 27601
```

Land on the confirmation: **paid**, with a real order id.

**Now switch tabs.** Stripe: the PaymentIntent, `succeeded`, `amount_received` matching.
Merchant Center: the order, with an `Authorization/Success` and a `Charge/Success`.

> "That's a real charge and a real commercetools order. Nobody expects that from an AI demo."

### 4. The back office — instant

Open the portal. The dashboard loads in under a second because **no model is involved** —
it is the same reads the agent uses, rendered directly.

Point at the **Conversion tile**: an em dash, not 0%.

> "commercetools records no sessions, so traffic and conversion don't exist. It says so
> instead of showing zero. A flat 0% reads as a catastrophic week, not a missing feed."

Then the queue: real derived alerts, including one at **−5 in stock** — genuinely oversold.

### 5. The staged change — about 13 seconds

Click **Draft restock** on **Classic Serving Tray (−5 in stock)**, then Send.

Read the card out loud when it lands:

- the agent's reasoning (it sizes the restock to clear the deficit, not to build cover it
  has no evidence for)
- the warning: **"has 160 unit(s) in other supply channels, which this storefront does not
  sell from"**
- **"Nothing applies until you approve."**

> "A SKU here has one inventory entry per supply channel — five of them. Judging any single
> one flagged seven well-stocked products as sold out. Judging the total hid five real
> shortages. What matters is the entry the cart draws on — and the agent tells you when the
> stock is just a channel away."

**Do not click Approve.** Ending on the refusal is the point. Click Dismiss if you want to
show the audit trail: "Dismissed by …".

### 6. Close — 30 seconds

> "Prompts, skills, tools and gates: untouched from Anthropic's reference. I wrote adapters.
> What took the time was nine things our own production data broke — all written back into
> the ES workspace overrides so the next person doesn't rediscover them."

## Timings, so nothing surprises you

| Step | Time | Why |
|---|---|---|
| Shop search turn | 9–12s | one search, then two presentation calls |
| Checkout, address → paid | ~40s of clicking | mostly you typing the card |
| Portal dashboard | **0.8s** | no model in the path |
| Portal digest turn (`What needs my attention`) | **18–23s** | seven tool calls, so seven model round trips |
| Draft restock turn | 12–13s | four tool calls |

**Avoid "What needs my attention this morning?" live.** It is the slowest turn in the build
and the dashboard already answers it instantly. Use the dashboard, then the restock.

## If something goes wrong

- **Payment fails with a generic error.** The connector's processor scales to zero. Retry —
  the session creation warms it, and the second attempt is usually clean.
- **"Your cart is empty" on /checkout.** The session id lives in `localStorage`; a private
  window or a cleared store starts a new guest. Add an item again.
- **Cart write fails with `ExtensionPredicateEvaluationFailed`.** An API Extension with an
  unguarded `custom(fields(...))` condition is back in the project. `check_extensions.py`
  names it; `CT_CART_CUSTOM_MARKER=1` works around it.
- **The portal shows stale staged changes.** `python scripts/demo_reset.py --confirm`, then
  restart the API.
- **A search returns something odd** (a chandelier under "drinking glass", a slug as a
  product title). That is real lab data. Say so — it is a better answer than a curated one.

## Questions you will get

**"Why not just point it at an MCP server?"** We had that. The tool loop runs in the model
provider's infrastructure, so a cart write has already happened by the time our code can
check it was scoped to the right shopper. You can block the reply, not the write. Here the
tools run in our process, so the provenance gate and the caps run *before* commercetools.

**"Could it place the order itself?"** Not in this build. The checkout tool renders the cart;
payment is the host application's own flow.

**"What stops it repricing the whole catalogue?"** Guardrails checked at staging and again
before the write — 20% per move, 25 items per change, 500 units per restock — plus host
approval, plus the write targeting only the price commercetools itself resolves.

**"Is this deployable?"** The agents, yes. Session state and the staged-change ledger are on
commercetools Custom Objects, so nothing is pinned to one process. What is not done:
real authentication at session start, and a spend cap on the model key.
