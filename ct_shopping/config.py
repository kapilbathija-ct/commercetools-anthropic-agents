"""The shopping agent's deployment config.

``domain_search_notes`` names only the dimensions the platform can actually filter on. On
this project ``color`` and ``finish`` are defined with ``isSearchable: false``, so a
platform-side filter on either returns zero matches with no error; the backend filters
them after the text match instead, and the note tells the model it may ask for them.
"""

from __future__ import annotations

from shopping_agent import ShoppingAgentConfig

# Derived from the live Product Types, and the only option names the catalogue's families
# actually vary on: color, finish, size and diameter-in-inches.
DOMAIN_SEARCH_NOTES = (
    "This catalogue is home furnishings, lighting and tableware. Useful filter attributes "
    "are size, color, finish and diameter-in-inches; pass them in filters.attributes with "
    "the shopper's own words. Most products are sold as a single record; a minority are "
    "families that vary by color, finish or size, and those need a variant id from the "
    "product's details before the cart will take them."
)


def build_shopping_config(**overrides: object) -> ShoppingAgentConfig:
    return ShoppingAgentConfig(
        brand_name="ACME Home",
        assistant_name="the ACME Home assistant",
        brand_voice="warm, concise, and plain about trade-offs",
        domain_search_notes=DOMAIN_SEARCH_NOTES,
        # Every system this catalogue has is wired; nothing is switched off.
        enable_cart=True,
        enable_orders=True,
        enable_policies=True,
        enable_fulfillment=True,
        # commercetools ids are UUIDs, and a purchasable record is addressed as
        # "{productId}#{variantId}". Both have to ground a catalogue read when a shopper
        # pastes one back, so the default SKU-shaped patterns are replaced.
        product_id_patterns=(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:#\d+)?\b",
            r"\b[A-Z]{2,4}-\d{3,5}\b",
        ),
        **overrides,
    )
