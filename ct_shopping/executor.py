"""The executor subclass, for the two commercetools states that are facts about the store
rather than outages.

Without these both arrive as a bare 400 and the model tells the shopper the tool is
temporarily unavailable, which is wrong and unactionable in each case.
"""

from __future__ import annotations

from commerce_common.execution import ToolOutcome
from shopping_agent.executor import ShoppingToolExecutor

from ct_common import CartFrozen, SignInRequired

FROZEN_TEXT = (
    "This shopper has a checkout already in progress, so the cart is locked. Tell them to "
    "finish or abandon that checkout before changing the cart."
)
SIGN_IN_TEXT = (
    "This needs a signed-in customer and the session is a guest. Ask the shopper to sign in."
)


class CTShoppingToolExecutor(ShoppingToolExecutor):
    def domain_error(self, error: Exception) -> ToolOutcome | None:
        if isinstance(error, CartFrozen):
            return ToolOutcome.error(FROZEN_TEXT)
        if isinstance(error, SignInRequired):
            return ToolOutcome.error(SIGN_IN_TEXT)
        return super().domain_error(error)
