*Claude's commerce agents, running on our own commercetools project — with real payments* :credit_card:

Anthropic open-sourced `commerce-agents` this week: a shopping agent and a merchant agent, defined once and runnable three ways. The demos ship against in-memory fixtures and a `checkout` tool that charges nothing. I pointed it at `kmb-core-commerce-lab` instead and ran it until a real card got charged.

*What's working* (all local, ~a day of work)
• *Shopping agent* over the real catalogue — 159 products, live prices, real carts. One turn searches, compares and fills a commercetools cart.
• *Real checkout* — commercetools Checkout in PaymentOnly mode on our already-deployed Stripe connector. Every run creates a real Order with `Authorization/Success` + `Charge/Success`, matched to a Stripe PaymentIntent showing `succeeded`. A declined card leaves a failed payment and *no* order.
• *Merchant agent* + portal — real sales/orders/AOV off Orders, derived low-stock and slow-mover alerts, and staged writes that only apply when the operator clicks Approve. Verified as a real price change and a real inventory move.
• 50 offline tests, 11 browser tests (incl. a declined card and an IDOR check).

*Why commercetools is the natural fit for this*
The reference doesn't ship a commerce platform — it ships an agent that *expects* one, behind a typed interface (`StorefrontBackend` / `MerchantBackend`). That makes the platform question concrete:
• *API-first:* every method on the interface already had a commercetools API behind it. Nothing had to be built to be called.
• *It's a real system of record:* the design assumes something on the other side enforces stock, price, tax and eligibility whatever the model says. Several of our own invariants stopped the build cold — which is exactly what you want.
• *Composable = the agent is just another client:* the agent, the storefront and the Merchant Center read the same catalogue and write the same carts. An approved price change showed up in the shop's next search. No sync, no "AI copy" of the catalogue to drift.

*How the ct-for-builders skills came in*
The five `commercetools-ai-plugins` skills are installed in my ES workspace, but the thing that actually carried this build was the *skill-override layer* on top of them — `.claude/skill-overrides/commercetools-{platform,checkout,storefront,commerce-patterns}.md`. Live-validated corrections with dates and exact error strings. Three that saved hours:
• Real Checkout rejects a Frozen cart outright, and `unfreezeCart` isn't idempotent.
• `manage_sessions` is a separate grant from `manage_project`, and API client scopes can't be edited after creation.
• `componentType: DropIn` + `type: embedded` is the *only* combination the Stripe connector's enabler accepts.

I added *8 new entries* back into those files, including one worth knowing widely: *`order.paymentState` is unset even after a successful charge* — both success transactions present, field still `null`. Report it raw and a fully paid order reads as unpaid. Derive it from the Payment's transactions.

Also found and cleared: *14 leftover `ext-lab-*` API Extensions with unguarded `custom(fields(...))` conditions were breaking every cart write in the project* — ours, the storefront's, anyone's.

Write-up with screenshots: <DOC_LINK>
Happy to demo it — takes two minutes end to end.
