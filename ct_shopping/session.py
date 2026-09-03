"""The session context the host builds once per session.

``user_id`` is the principal and the only place identity lives: a registered Customer's id
when ``is_guest`` is false, otherwise the anonymous id this session is known by. No route
and no tool argument carries either -- the backend reads them from here and attaches them
to the commercetools call server-side.

The distinction is not cosmetic. A commercetools Cart's ``customerId`` is only valid for a
real registered Customer; a guest's identity has to go in ``anonymousId`` instead, and a
cart created with the wrong one silently ends up with neither field set.
"""

from __future__ import annotations

from shopping_agent import ShoppingSessionContext


class CTShoppingSession(ShoppingSessionContext):
    is_guest: bool = True

    @property
    def customer_id(self) -> str | None:
        return None if self.is_guest else self.user_id

    @property
    def anonymous_id(self) -> str | None:
        return self.user_id if self.is_guest else None

    def cart_owner_predicate(self) -> str:
        """The commercetools predicate that finds this shopper's own active cart."""
        field = "anonymousId" if self.is_guest else "customerId"
        return f'{field}="{self.user_id}" and cartState="Active"'

    def cart_owner_fields(self) -> dict[str, str]:
        field = "anonymousId" if self.is_guest else "customerId"
        return {field: self.user_id}
