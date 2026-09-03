"""Exercise every StorefrontBackend method against the live project.

Read-only apart from one cart, which is created under an anonymous id of its own and left
for inspection. Prints what each method returned and how large its fenced result would be.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shopping_agent import SearchFilters  # noqa: E402

from ct_common import CTClient, load_settings  # noqa: E402
from ct_shopping import CommercetoolsStorefront, CTShoppingSession  # noqa: E402


async def main() -> None:
    settings = load_settings()
    client = CTClient(settings)
    backend = CommercetoolsStorefront(client, checkout_url="http://localhost:3000/checkout")
    guest = CTShoppingSession(
        session_id="probe-" + uuid.uuid4().hex[:8],
        user_id="probe-anon-" + uuid.uuid4().hex[:8],
        is_guest=True,
    )
    try:
        print("== search_products('chair') ==")
        products = await backend.search_products(guest, "chair", limit=5)
        for p in products:
            shape = "family" if p.options else "plain"
            print(f"  [{shape}] {p.title}  ${p.price}  stock={p.in_stock}  {p.product_id}")
            if p.options:
                print(f"          options={p.options}")

        print("\n== search_products with an unsearchable attribute filter (color) ==")
        filtered = await backend.search_products(
            guest, "chair", SearchFilters(attributes={"color": "green"}), limit=5
        )
        print(f"  {len(filtered)} of {len(products)} kept: {[p.title for p in filtered]}")

        print("\n== search_products with a price ceiling ==")
        cheap = await backend.search_products(
            guest, "chair", SearchFilters(max_price=100.0, sort="price_asc"), limit=5
        )
        print(f"  {[(p.title, p.price) for p in cheap]}")

        family = next((p for p in products if p.options), None)
        target = family or products[0]
        print(f"\n== get_product_details({target.product_id}) ==")
        details = await backend.get_product_details(guest, target.product_id)
        print(f"  {details.title}  ${details.price}  variants={len(details.variants)}")
        for v in details.variants[:3]:
            print(f"    {v.product_id}  ${v.price}  {v.option_values}  stock={v.in_stock}")
        print(f"  fenced size: {len(details.model_dump_json())} chars")

        buyable = details.variants[0].product_id if details.variants else details.product_id
        print(f"\n== add_to_cart({buyable}, 2) as a guest ==")
        cart = await backend.add_to_cart(guest, buyable, 2)
        print(f"  {cart.item_count} units, subtotal ${cart.subtotal} {cart.currency}")
        for item in cart.items:
            print(f"    {item.title} x{item.quantity} @ ${item.price} = ${item.line_total}")

        print("\n== update_cart_item -> 1, then get_cart ==")
        await backend.update_cart_item(guest, buyable, 1)
        cart = await backend.get_cart(guest)
        print(f"  {cart.item_count} units, subtotal ${cart.subtotal}")

        print("\n== checkout_handoff ==")
        print(f"  {[h.url for h in await backend.checkout_handoff(guest, cart)]}")

        print("\n== get_fulfillment_options (matching this cart) ==")
        for option in (await backend.get_fulfillment_options(guest, [buyable]))[:5]:
            print(f"  {option.location}: ${option.fee}  eta={option.eta}")

        print("\n== search_policies('return a damaged chair') ==")
        for policy in await backend.search_policies(guest, "return a damaged chair"):
            print(f"  {policy.policy_id}: {policy.title}")

        print("\n== get_preferences (guest) ==")
        print(f"  {await backend.get_preferences(guest)}")

        print("\n== get_orders as a guest (must refuse, not fail) ==")
        try:
            await backend.get_orders(guest)
            print("  ERROR: returned instead of refusing")
        except Exception as error:
            print(f"  {type(error).__name__}: {error}")

        # A real customer with orders, to exercise the signed-in path.
        customers = await client.get("/customers", {"limit": 20})
        orders = await client.get("/orders", {"limit": 20, "where": "customerId is defined"})
        customer_ids = {
            o.get("customerId") for o in orders.get("results", []) if o.get("customerId")
        }
        chosen = next((c for c in customers.get("results", []) if c["id"] in customer_ids), None)
        if chosen is None:
            print("\n== no customer with orders found; signed-in path not exercised ==")
            return
        member = CTShoppingSession(
            session_id=guest.session_id, user_id=chosen["id"], is_guest=False
        )
        print(f"\n== get_preferences (signed in: {chosen.get('email')}) ==")
        print(f"  {await backend.get_preferences(member)}")
        print("\n== get_orders (signed in) ==")
        history = await backend.get_orders(member, limit=3)
        for order in history:
            print(
                f"  {order.order_id}  {order.status}  ${order.total}  "
                f"{len(order.items)} items  {order.placed_at.date()}"
            )
        if history:
            one = await backend.get_order(member, history[0].order_id)
            print(f"\n== get_order({history[0].order_id}) -> {one.order_id if one else None} ==")
            print("\n== get_order with another shopper's id (must be None) ==")
            print(f"  {await backend.get_order(member, 'not-this-customers-order')}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
