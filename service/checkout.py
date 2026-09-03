"""The checkout routes.

Checkout is deliberately outside the agent's tool surface. The agent's ``checkout`` tool
renders the cart and nothing more; these routes are the host's own flow, driven by the
customer in the UI, and they are what the checkout card links to. No agent tool can reach
any of them, and nothing here is reachable without the session's own id.

The sequence, in the order the platform requires it:

1. address on the cart, and the cart made Active if a previous attempt left it Frozen
2. shipping method chosen from the ones that match the cart
3. a Checkout Session created as late as possible, since sessions expire
4. the widget mounted in the browser; Checkout creates the Payment and the Order itself
5. the confirmation read back, scoped to this session's own principal
"""

# Route parameters are annotated with dependencies built at call time, so this module
# evaluates its annotations eagerly (no ``from __future__ import annotations``).

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ct_common import CTError
from ct_common.checkout import CheckoutSessions

logger = logging.getLogger(__name__)


class AddressRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    street_name: str = Field(min_length=1, max_length=160)
    street_number: str | None = Field(default=None, max_length=20)
    city: str = Field(min_length=1, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(min_length=2, max_length=2)

    def to_ct(self) -> dict[str, Any]:
        return {
            "firstName": self.first_name,
            "lastName": self.last_name,
            "email": self.email,
            "streetName": self.street_name,
            "streetNumber": self.street_number,
            "city": self.city,
            "state": self.state,
            "postalCode": self.postal_code,
            "country": self.country,
        }


class ShippingMethodRequest(BaseModel):
    shipping_method_id: str = Field(min_length=1, max_length=64)


def install_checkout_routes(
    app: Any,
    *,
    backend: Any,
    sessions: CheckoutSessions,
    current: Any,
    context: Any,
) -> None:
    router = APIRouter(prefix="/api/checkout")
    CurrentSession = current

    @router.post("/address")
    async def set_address(request: AddressRequest, record: CurrentSession) -> dict:
        try:
            await backend.prepare_for_checkout(
                context(record), address=request.to_ct(), email=request.email
            )
        except CTError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        methods = await backend.checkout_shipping_methods(context(record))
        return {"ok": True, "shipping_methods": methods}

    @router.get("/shipping-methods")
    async def shipping_methods(record: CurrentSession) -> dict:
        return {"shipping_methods": await backend.checkout_shipping_methods(context(record))}

    @router.post("/shipping-method")
    async def set_shipping_method(request: ShippingMethodRequest, record: CurrentSession) -> dict:
        try:
            cart = await backend.set_shipping_method(context(record), request.shipping_method_id)
        except CTError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        total = cart.get("totalPrice") or {}
        # ``totalPrice`` is the right number to show *here*: at this point it legitimately
        # includes the shipping just chosen. It is only wrong as a pre-shipping subtotal.
        digits = total.get("fractionDigits", 2)
        return {
            "ok": True,
            "total": round((total.get("centAmount") or 0) / (10**digits), 2),
            "currency": total.get("currencyCode"),
        }

    @router.post("/session")
    async def create_session(record: CurrentSession) -> dict:
        session = context(record)
        cart = await backend.cart_for_checkout(session)
        if not cart or not cart.get("lineItems"):
            raise HTTPException(status_code=400, detail="There is nothing in the cart")
        # The session is scoped to this caller's own cart, resolved server-side from the
        # session id -- never a cart id supplied by the client, which would be an IDOR.
        await sessions.warm_processor()
        try:
            created = await sessions.create(cart["id"])
        except CTError as error:
            logger.exception("checkout session creation failed")
            raise HTTPException(status_code=502, detail=str(error)) from error
        return created

    @router.get("/order/{order_id}")
    async def confirmation(order_id: str, record: CurrentSession) -> dict:
        """The confirmation read, scoped to this session's principal: an order id off a URL
        with no ownership check is an IDOR, which is a mistake this project has made once
        already."""
        order = await backend.order_for_session(context(record), order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        payment_state = await backend.payment_state_for_order(order)
        taxed = ((order.get("taxedPrice") or {}).get("totalGross")) or order.get("totalPrice") or {}
        digits = taxed.get("fractionDigits", 2)
        return {
            "order_id": order["id"],
            "order_number": order.get("orderNumber"),
            "state": order.get("orderState"),
            "payment_state": payment_state,
            # What the customer was actually charged, not the pre-tax cart field.
            "total": round((taxed.get("centAmount") or 0) / (10**digits), 2),
            "currency": taxed.get("currencyCode"),
            "items": [
                {
                    "title": next(
                        (
                            v
                            for k, v in (line.get("name") or {}).items()
                            if k.startswith("en") and v
                        ),
                        next(iter((line.get("name") or {}).values()), ""),
                    ),
                    "quantity": line.get("quantity"),
                }
                for line in order.get("lineItems") or []
            ],
        }

    app.include_router(router)
