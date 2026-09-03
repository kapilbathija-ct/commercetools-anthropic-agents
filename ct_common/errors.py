"""The domain errors each role's executor maps, so the model reads a fact about the store
instead of "the tool is temporarily unavailable".

Every class here exists because the failure it names was hit live on this project and
surfaces as an indistinguishable 400 otherwise.
"""

from __future__ import annotations


class CTError(RuntimeError):
    """A commercetools call that failed for a reason the caller cannot act on. Carries the
    status and the platform's own error code for the log, never for the model."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class CartFrozen(CTError):
    """``400 InvalidOperation "The cart is frozen"``. A cart abandoned partway through
    checkout stays Frozen and real Checkout rejects it outright
    (``CartInvalidStateError``), so this must not read as a generic failure: the shopper
    has a checkout still open."""


class TaxCategoryMissing(CTError):
    """A line item's product has no Tax Category, which blocks cart creation at cart time
    rather than at product-save time. ``scripts/readiness.py`` lists the products this
    would hit before a demo does."""


class ProductNotPurchasable(CTError):
    """The record exists but cannot be bought in this context. Raised so the shopping
    backend can re-raise it as the reference's ``Unavailable``, whose message names ids
    only."""


class SignInRequired(CTError):
    """A read that needs a registered Customer arrived on a guest session. A Cart's
    ``customerId`` only accepts a real Customer, so a guest's identity lives in
    ``anonymousId`` and order history simply does not exist for one."""
