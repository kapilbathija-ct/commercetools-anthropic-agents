# Loom scripts — three clips, ~8 minutes of footage

Record three separate clips, not one long one. Short clips get watched and get forwarded.

`[DO]` = what you click. `[SAY]` = narration. `[FILLER]` = something true to say while a
turn is running, so there's no dead air.

---

## Pre-flight — 3 minutes, do this once

```bash
python scripts/demo_reset.py --confirm
# restart the API so the in-process ledger is empty too
```

Everything is already running and both agents are warmed. Confirm:
- shop http://localhost:3005 · portal http://localhost:3105 · API :8000
- Stripe dashboard open in a tab, **test mode**, on Payments
- Merchant Center open on Orders

**Browser hygiene for recording:** full-screen the window, hide bookmarks, close the other
tabs you don't need on camera. The shop and portal are running production builds, so
there's no Next.js dev badge in shot.

---

## Clip 1 — "A shopping agent on real commercetools data" (~2 min)

**[DO]** Start on http://localhost:3005.

**[SAY]**
> "This is Anthropic's open-source shopping agent, running against our own commercetools
> project — 159 real products, real prices, real carts. Not fixtures."

**[DO]** Click the starter **"A lounge chair for a small living room, under $250"**.

**[FILLER]** — you have about 10 seconds here:
> "While that runs: every price you're about to see is what a customer actually pays. 62 of
> our 159 products carry an applied product discount. If you read the list price field —
> the obvious one — you misquote a third of the catalogue."

**[DO]** When the cards land, hover one.

**[SAY]**
> "Real products, real photos, real post-discount prices. And the chips underneath are the
> agent proposing the next question rather than waiting for one."

**[DO]** Click **Add to cart** on one of the cards. Point at the cart panel.

**[SAY]**
> "That's a real commercetools cart. And notice — free shipping over $500, which is our
> actual shipping-method threshold, not a number in the demo code."

**End the clip there.** Don't start checkout in this one.

---

## Clip 2 — "A real payment, through commercetools Checkout and Stripe" (~3 min)

This is the one people will forward. Start with an item already in the cart.

**[SAY]**
> "Most agent demos stop at the cart. Let's actually pay."

**[DO]** Click **Check out** in the cart panel.

**[FILLER]** — about 10 seconds:
> "The agent has a checkout tool, but all it does is render the cart. It cannot take a
> payment. That's deliberate in the reference design, and I kept it."

**[DO]** When the card appears, point at **"Not charged"**.

**[SAY]**
> "Itemised, and explicitly 'Not charged'. Payment is the host application's job."

**[DO]** Click **Continue to checkout**. Fill the address — talk while typing:

**[SAY]**
> "commercetools won't create an order without a shipping address, so this step isn't
> decoration."

**[DO]** Save address → the delivery step appears.

**[SAY]**
> "These are our five real shipping methods, with the actual free-above thresholds."

**[DO]** Pick **Economy Ground Shipping** → Continue to payment.

**[SAY]**
> "And this is the real thing: commercetools Checkout in payment-only mode, on the Stripe
> connector we already had deployed. That's Stripe's own payment element."

**[DO]** Type the card. Say the numbers as you type so viewers know it's a test card:
```
4242 4242 4242 4242   12/34   123   27601
```

**[DO]** Complete purchase. Land on the confirmation.

**[SAY]**
> "Paid. Real order id, and the total is what was actually charged including tax."

**[DO]** **Switch to the Stripe tab.** Find the payment at the top.

**[SAY]**
> "There it is in Stripe — succeeded, amount received, test mode."

**[DO]** **Switch to Merchant Center → Orders.** Open the newest order.

**[SAY]**
> "And the same thing in commercetools: a real order, with an authorization and a charge
> transaction against it. That's the whole loop, and it took a day."

---

## Clip 3 — "A merchant agent that asks permission" (~2.5 min)

**[DO]** Open http://localhost:3105.

**[SAY]**
> "Same reference, other half: the back office. This dashboard is under a second, because
> there's no model in the path — it's the same reads the agent uses, rendered directly."

**[DO]** Point at the **Conversion** tile.

**[SAY]**
> "Look at conversion. It's a dash, not zero. commercetools doesn't record sessions, so
> traffic and conversion genuinely don't exist here — and the agent says so instead of
> showing 0%. A flat zero reads as a catastrophic week, not a missing feed. Same for margin:
> there's no unit cost stored, so it won't invent one."

**[DO]** Scroll to **Needs you today**. Point at **Classic Serving Tray, −5 in stock**.

**[SAY]**
> "These alerts are derived — commercetools has no alerts object. And that one is genuinely
> oversold, minus five."

**[DO]** Click **Draft restock** on it, then **Send**.

**[FILLER]** — about 13 seconds:
> "While that runs, the thing that took me longest today: a SKU in commercetools has one
> inventory entry per supply channel, and ours have up to five. Judge any single one and you
> flag seven perfectly well-stocked products as sold out. Judge the total and you hide five
> real shortages. What matters is the entry the cart actually draws on."

**[DO]** When the card lands, read from it:

**[SAY]**
> "It sized the restock to clear the deficit and reach the threshold — not to build cover
> for demand it has no evidence for, and it says that. Then this line:"

**[DO]** Point at the guardrail note.

**[SAY]**
> "'160 units in other supply channels, which this storefront does not sell from.' That's
> the warning that stops you reordering stock you already own."

**[DO]** Point at Approve / Dismiss. **Do not click Approve.**

**[SAY]**
> "And then it stops. Every merchant write in this build is a proposal. Approve only works
> for a change a human clicked — nothing typed in the chat can approve anything. That's
> where I'd leave it."

**End on the un-clicked Approve button.** That's the shot.

---

## Rules for all three

- **Never say "let me wait for this"** — use the `[FILLER]` lines. Every wait has one.
- **Don't run "What needs my attention this morning?" on camera.** It's the slowest turn in
  the build (18–23 seconds) and the dashboard already answers it instantly.
- **If a search returns something odd** — a chandelier under "drinking glass", a product
  whose title is a slug — say so. "That's real lab data, and the agent doesn't pretend
  otherwise" is a better line than a reshoot.
- **If payment fails once**, retry. The connector's processor scales to zero and the first
  hit can be cold. Second attempt is clean.
- **Between takes**, if you staged a change: `python scripts/demo_reset.py --confirm` and
  restart the API.

## Timings, so you can plan takes

| Clip | Footage | Longest single wait |
|---|---|---|
| 1 — shopping agent | ~2 min | 10s (search turn) |
| 2 — real payment | ~3 min | your typing, then ~8s for the widget |
| 3 — merchant + approval | ~2.5 min | 13s (restock turn) |

Three clips, one take each, is about 12 minutes including resets. You have room for a
second take on clip 2.
