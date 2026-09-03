"""``StorefrontBackend`` over commercetools.

Every method calls commercetools server-side with the credential this process holds and
returns the agent's own records; the model never sees a token, a URL or a raw projection.
The cart writes are the only writes, and each arrives after the executor's provenance gate
and quantity caps have already run -- which is the reason this project calls the platform
itself rather than letting the model reach a commercetools MCP server directly. When the
tool loop runs inside the model provider's infrastructure, a cart write has already
happened by the time any of our code can check it was scoped to the right shopper.

Where a decision here looks unusual, it is because the live project taught it:

* Search filters on attributes are applied **after** the platform's own text match, not as
  a Product Search filter. An attribute with ``isSearchable: false`` makes a
  platform-side filter return zero matches with no error, and on this project ``color``
  and ``finish`` -- the two a shopper is most likely to name -- are both unsearchable.
* Carts are created with the shopper's identity in ``customerId`` or ``anonymousId``, and
  in a Store when one is configured. There is no update action that sets ``store`` on an
  existing cart, so anything Store-scoped has to be right at creation or the cart must be
  thrown away.
* A product with no Tax Category blocks cart creation, and the failure arrives at cart
  time rather than product-save time. It is mapped to ``Unavailable`` so the shopper is
  told the item cannot be bought rather than that something broke.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from shopping_agent import (
    Cart,
    CartItem,
    CheckoutHandoff,
    FulfillmentOption,
    NotOffered,
    Order,
    OrderItem,
    OrderStatus,
    Policy,
    ProductDetails,
    SearchFilters,
    StorefrontBackend,
    Unavailable,
    UserPreferences,
)

from ct_common import CTClient, CTError, ReferenceCache, TaxCategoryMissing
from ct_common import graphql as g
from ct_common.mapping import (
    localized,
    money,
    parse_ref,
    to_product,
    to_product_details,
    variant_ref,
)

from .session import CTShoppingSession

logger = logging.getLogger(__name__)

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_uuid(value: str) -> bool:
    return bool(_UUID_PATTERN.match(value))


POLICY_CONTAINER = "policy-content"

# A workaround with an end date, off by default and enabled with
# ``CT_CART_CUSTOM_MARKER=1``. An API Extension whose trigger condition reads
# ``custom(fields(...))`` with no ``is defined`` guard cannot be evaluated against a cart
# that has no custom object, so while such an extension is registered *every* cart Update
# in the project fails with ``ExtensionPredicateEvaluationFailed``. Setting one custom
# field whose value matches no condition makes each predicate evaluate false and no
# extension fire. ``scripts/ensure_cart_type.py`` creates the Type; turn the flag off once
# no unguarded extension is registered, and ``scripts/check_extensions.py`` says whether
# any still is.
CART_TYPE_KEY = "agent-cart-marker"
CART_TYPE_FIELD = "extLabTest"
CART_TYPE_VALUE = "commerce-agent"

# commercetools order states mapped onto the agent's own vocabulary. Shipment state leads
# because it is what a shopper is asking about; the order state only decides the ends.
_SHIPMENT_STATUS = {
    "Shipped": OrderStatus.SHIPPED,
    "Delivered": OrderStatus.DELIVERED,
    "Ready": OrderStatus.PROCESSING,
    "Pending": OrderStatus.PROCESSING,
    "Delayed": OrderStatus.DELAYED,
    "Partial": OrderStatus.SHIPPED,
    "Backorder": OrderStatus.DELAYED,
}


class CommercetoolsStorefront(StorefrontBackend):
    def __init__(
        self,
        client: CTClient,
        *,
        cache: ReferenceCache | None = None,
        checkout_url: str | None = None,
        cart_marker: bool | None = None,
    ) -> None:
        self._client = client
        self._settings = client.settings
        self._cache = cache or ReferenceCache()
        self._checkout_url = checkout_url or os.environ.get("STOREFRONT_CHECKOUT_URL")
        if cart_marker is None:
            cart_marker = os.environ.get("CT_CART_CUSTOM_MARKER", "0") == "1"
        self._cart_marker = cart_marker

    # -- Reference data ------------------------------------------------------------------

    async def attribute_types(self) -> dict[str, str]:
        """Attribute name to its Product Type type name. An attribute name binds to
        exactly one type project-wide, so one flat map is correct and one read serves the
        process."""

        async def load() -> dict[str, str]:
            payload = await self._client.get("/product-types", {"limit": 100})
            return {
                attribute["name"]: attribute["type"]["name"]
                for product_type in payload.get("results", [])
                for attribute in product_type.get("attributes", [])
            }

        return await self._cache.get("product-types", load)

    def _locale_vars(self) -> dict[str, Any]:
        return {
            "locale": self._settings.locale,
            "currency": self._settings.currency,
            "country": self._settings.country,
        }

    # -- Catalog -------------------------------------------------------------------------

    async def search_products(
        self,
        session: CTShoppingSession,
        query: str,
        filters: SearchFilters | None = None,
        limit: int = 8,
    ) -> list[Any]:
        # Over-fetch so the attribute and price filters below still have candidates to keep.
        fetch = min(max(limit * 4, limit), 40)
        data = await self._client.graphql(
            g.SEARCH_QUERY,
            {
                "text": query or None,
                "limit": fetch,
                "filters": None,
                "sorts": None,
                **self._locale_vars(),
            },
        )
        results = (data.get("productProjectionSearch") or {}).get("results") or []
        types = await self.attribute_types()
        products = []
        for raw in results:
            projection = g.normalize_projection(raw)
            products.append(
                to_product(
                    projection,
                    types,
                    self._settings.locale,
                    self._settings.currency,
                    g.category_label(raw),
                )
            )
        return self._apply_filters(products, filters)[:limit]

    def _apply_filters(self, products: list[Any], filters: SearchFilters | None) -> list[Any]:
        """Price, rating, category and attribute filters, applied here rather than by the
        platform. A Product Search filter on an attribute whose definition has
        ``isSearchable: false`` returns an empty result set and no error, which reads to
        the model as "the store has none of those"."""
        if filters is None:
            return products
        kept = []
        for product in products:
            if filters.min_price is not None and product.price < filters.min_price:
                continue
            if filters.max_price is not None and product.price > filters.max_price:
                continue
            if filters.min_rating is not None and (product.rating or 0) < filters.min_rating:
                continue
            if (
                filters.category
                and filters.category.lower() not in (product.category or "").lower()
            ):
                continue
            if not self._attributes_match(product, filters.attributes):
                continue
            kept.append(product)
        if filters.sort == "price_asc":
            kept.sort(key=lambda p: p.price)
        elif filters.sort == "price_desc":
            kept.sort(key=lambda p: p.price, reverse=True)
        elif filters.sort == "rating":
            kept.sort(key=lambda p: p.rating or 0, reverse=True)
        return kept

    @staticmethod
    def _attributes_match(product: Any, wanted: dict[str, str]) -> bool:
        for name, value in wanted.items():
            needle = value.strip().lower()
            haystack = [product.attributes.get(name, "")]
            haystack += product.options.get(name, [])
            if not any(needle in candidate.lower() for candidate in haystack if candidate):
                return False
        return True

    async def get_product_details(
        self, session: CTShoppingSession, product_id: str
    ) -> ProductDetails | None:
        base_id, variant_id = parse_ref(product_id)
        data = await self._client.graphql(g.PRODUCT_QUERY, {"id": base_id, **self._locale_vars()})
        product = data.get("product")
        if not product:
            return None
        current = (product.get("masterData") or {}).get("current") or {}
        projection = g.normalize_projection({**current, "id": product["id"]})
        types = await self.attribute_types()
        return to_product_details(
            projection,
            types,
            self._settings.locale,
            self._settings.currency,
            variant_id=variant_id,
            category_name=g.category_label(current),
        )

    # -- Cart ----------------------------------------------------------------------------

    async def _find_cart(self, session: CTShoppingSession) -> dict[str, Any] | None:
        data = await self._client.graphql(
            g.FIND_CART_QUERY,
            {"where": session.cart_owner_predicate(), "locale": self._settings.locale},
        )
        results = (data.get("carts") or {}).get("results") or []
        return results[0] if results else None

    async def _create_cart(self, session: CTShoppingSession) -> dict[str, Any]:
        """Currency, country and the shopper's identity are creation-time decisions: a
        cart's currency is immutable afterwards, and ``store`` has no update action at all.
        """
        draft: dict[str, Any] = {
            "currency": self._settings.currency,
            "country": self._settings.country,
            "locale": self._settings.locale,
            **session.cart_owner_fields(),
        }
        if self._settings.store_key:
            draft["store"] = {"typeId": "store", "key": self._settings.store_key}
        if self._cart_marker:
            draft["custom"] = {
                "type": {"typeId": "type", "key": CART_TYPE_KEY},
                "fields": {CART_TYPE_FIELD: CART_TYPE_VALUE},
            }
        created = await self._client.post("/carts", draft)
        return await self._cart_by_id(created["id"])

    async def _cart_by_id(self, cart_id: str) -> dict[str, Any]:
        data = await self._client.graphql(
            g.CART_BY_ID_QUERY, {"id": cart_id, "locale": self._settings.locale}
        )
        return data.get("cart") or {}

    def _to_cart(self, raw: dict[str, Any] | None) -> Cart:
        """The agent's cart. The subtotal is summed from the line items by the record
        itself; ``cart.totalPrice`` is deliberately not read, because it silently starts
        including the shipping cost the moment a shipping method is set."""
        if not raw:
            return Cart(currency=self._settings.currency)
        items = []
        for line in raw.get("lineItems") or []:
            variant = g.normalize_variant(line.get("variant"))
            images = variant.get("images") or []
            items.append(
                CartItem(
                    product_id=variant_ref(line["productId"], variant.get("id", 1)),
                    title=localized(line.get("name"), self._settings.locale),
                    price=money(line.get("price")),
                    quantity=line.get("quantity", 1),
                    image_url=images[0]["url"] if images else None,
                )
            )
        total = raw.get("totalPrice") or {}
        return Cart(items=items, currency=total.get("currencyCode") or self._settings.currency)

    async def get_cart(self, session: CTShoppingSession) -> Cart:
        return self._to_cart(await self._find_cart(session))

    async def _update(self, cart: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
        updated = await self._client.post(
            f"/carts/{cart['id']}", {"version": cart["version"], "actions": actions}
        )
        return await self._cart_by_id(updated.get("id", cart["id"]))

    async def add_to_cart(self, session: CTShoppingSession, product_id: str, quantity: int) -> Cart:
        base_id, variant_id = parse_ref(product_id)
        if variant_id is None:
            # The executor holds a family before this point; a bare product id here means
            # the caller has not resolved a variant, and commercetools cannot add one.
            raise Unavailable(
                f"{product_id} is a product family; add one of the variant ids from its details"
            )
        cart = await self._find_cart(session) or await self._create_cart(session)
        action = {
            "action": "addLineItem",
            "productId": base_id,
            "variantId": variant_id,
            "quantity": quantity,
        }
        try:
            updated = await self._update(cart, [action])
        except TaxCategoryMissing as error:
            # Surfaces at cart time, never at product-save time.
            logger.warning("tax category missing for %s: %s", product_id, error)
            raise Unavailable(f"{product_id} cannot be added to the cart right now") from error
        return self._to_cart(updated)

    async def _line_item_id(self, cart: dict[str, Any], product_id: str) -> str | None:
        """commercetools cart writes take a line item id, while the agent's contract takes
        the product id the model saw."""
        base_id, variant_id = parse_ref(product_id)
        for line in cart.get("lineItems") or []:
            variant = line.get("variant") or {}
            if line.get("productId") == base_id and (
                variant_id is None or variant.get("id") == variant_id
            ):
                return line["id"]
        return None

    async def update_cart_item(
        self, session: CTShoppingSession, product_id: str, quantity: int
    ) -> Cart:
        cart = await self._find_cart(session)
        if not cart:
            return self._to_cart(None)
        line_item_id = await self._line_item_id(cart, product_id)
        if line_item_id is None:
            return self._to_cart(cart)
        updated = await self._update(
            cart,
            [
                {
                    "action": "changeLineItemQuantity",
                    "lineItemId": line_item_id,
                    "quantity": quantity,
                }
            ],
        )
        return self._to_cart(updated)

    async def remove_from_cart(self, session: CTShoppingSession, product_id: str) -> Cart:
        cart = await self._find_cart(session)
        if not cart:
            return self._to_cart(None)
        line_item_id = await self._line_item_id(cart, product_id)
        if line_item_id is None:
            return self._to_cart(cart)
        updated = await self._update(
            cart, [{"action": "removeLineItem", "lineItemId": line_item_id}]
        )
        return self._to_cart(updated)

    async def checkout_handoff(
        self, session: CTShoppingSession, cart: Cart
    ) -> list[CheckoutHandoff]:
        """The storefront owns checkout: it drives the real Checkout Sessions API and the
        payment connector. The URL is added to the card's payload after the model's call,
        so it is never a tool argument and never reaches the model."""
        if not self._checkout_url or not cart.items:
            return []
        return [CheckoutHandoff(url=self._checkout_url, label="Continue to checkout")]

    # -- Customer context ----------------------------------------------------------------

    async def get_preferences(self, session: CTShoppingSession) -> UserPreferences:
        if session.is_guest:
            return UserPreferences(user_id=session.user_id, display_name=None)
        customer = await self._client.get(f"/customers/{session.user_id}")
        if not customer:
            return UserPreferences(user_id=session.user_id)
        addresses = customer.get("addresses") or []
        default_location = None
        for address in addresses:
            if address.get("id") == customer.get("defaultShippingAddressId"):
                default_location = ", ".join(
                    part for part in (address.get("city"), address.get("country")) if part
                )
        name = " ".join(
            part for part in (customer.get("firstName"), customer.get("lastName")) if part
        )
        return UserPreferences(
            user_id=session.user_id,
            display_name=name or None,
            loyalty_tier=(customer.get("customerGroup") or {}).get("id"),
            default_location=default_location,
        )

    # -- Orders --------------------------------------------------------------------------

    def _to_order(self, raw: dict[str, Any]) -> Order:
        """``taxedPrice.totalGross`` is what the customer was charged; ``totalPrice`` is
        the same pre-tax field a cart carries."""
        taxed = ((raw.get("taxedPrice") or {}).get("totalGross")) or {}
        fallback = raw.get("totalPrice") or {}
        total_source = taxed or fallback
        status = _SHIPMENT_STATUS.get(raw.get("shipmentState") or "")
        if raw.get("orderState") == "Cancelled":
            status = OrderStatus.CANCELLED
        elif status is None:
            status = OrderStatus.PROCESSING
        items = [
            OrderItem(
                product_id=variant_ref(line["productId"], (line.get("variant") or {}).get("id", 1)),
                title=localized(line.get("name"), self._settings.locale),
                quantity=line.get("quantity", 1),
                price=money(line.get("price")),
            )
            for line in raw.get("lineItems") or []
        ]
        return Order(
            order_id=raw.get("orderNumber") or raw["id"],
            status=status,
            placed_at=raw["createdAt"],
            items=items,
            total=money({"value": total_source}) if total_source else 0.0,
            currency=total_source.get("currencyCode") or self._settings.currency,
        )

    async def get_orders(self, session: CTShoppingSession, limit: int = 5) -> list[Order]:
        if session.is_guest:
            # A guest has no order history to read; the executor turns this into an ask to
            # sign in rather than an outage.
            raise NotOffered("order history needs a signed-in customer")
        data = await self._client.graphql(
            g.ORDERS_QUERY,
            {
                "where": f'customerId="{session.user_id}"',
                "limit": limit,
                "locale": self._settings.locale,
            },
        )
        return [self._to_order(raw) for raw in (data.get("orders") or {}).get("results") or []]

    async def get_order(self, session: CTShoppingSession, order_id: str) -> Order | None:
        if session.is_guest:
            raise NotOffered("order lookup needs a signed-in customer")
        # Scoped to this customer, so another shopper's order id resolves to nothing.
        # ``id`` enters the predicate only when the value is actually a UUID:
        # comparing ``id`` to anything else is rejected outright as a malformed
        # parameter, and the contract here is None for an id the store does not know.
        escaped = order_id.replace('"', "")
        clauses = [f'orderNumber="{escaped}"']
        if _is_uuid(escaped):
            clauses.append(f'id="{escaped}"')
        joined = " or ".join(clauses)
        where = f'customerId="{session.user_id}" and ({joined})'
        data = await self._client.graphql(
            g.ORDER_QUERY, {"where": where, "locale": self._settings.locale}
        )
        results = (data.get("orders") or {}).get("results") or []
        return self._to_order(results[0]) if results else None

    # -- Policies ------------------------------------------------------------------------

    async def search_policies(self, session: CTShoppingSession, query: str) -> list[Policy]:
        """commercetools has no policy-content system, so the passages live in Custom
        Objects under one container and are cached: a config-backing Custom Object
        re-fetched per request is exactly the redundant traffic a performance review flags.
        """

        async def load() -> list[dict[str, Any]]:
            payload = await self._client.get(f"/custom-objects/{POLICY_CONTAINER}", {"limit": 100})
            return payload.get("results") or []

        stored = await self._cache.get("policies", load)
        terms = [term for term in query.lower().split() if len(term) > 2]
        matches = []
        for entry in stored:
            value = entry.get("value") or {}
            haystack = f"{value.get('title', '')} {value.get('content', '')}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score or not terms:
                matches.append(
                    (
                        score,
                        Policy(
                            policy_id=entry.get("key", ""),
                            title=value.get("title", entry.get("key", "")),
                            category=value.get("category"),
                            content=value.get("content", ""),
                        ),
                    )
                )
        matches.sort(key=lambda pair: pair[0], reverse=True)
        return [policy for _, policy in matches[:5]]

    # -- Fulfillment ---------------------------------------------------------------------

    async def get_fulfillment_options(
        self, session: CTShoppingSession, product_ids: list[str]
    ) -> list[FulfillmentOption]:
        """Shipping methods matching this shopper's own cart when there is one, otherwise
        the country's methods. A displayed price accounts for ``freeAbove`` and the rate
        tiers; this project's methods use a ``CartValue`` rate input and several go free
        above a threshold, so the flat rate alone would misquote them."""
        methods = await self._matching_methods(session)
        options = []
        for method in methods:
            fee, note = self._shipping_fee(method)
            eta = ((method.get("custom") or {}).get("fields") or {}).get("eta") or note
            options.append(
                FulfillmentOption(
                    method="shipping",
                    eta=eta or "as quoted at checkout",
                    fee=fee,
                    location=method.get("name"),
                )
            )
        return options[:10]

    async def _matching_methods(self, session: CTShoppingSession) -> list[dict[str, Any]]:
        """``/shipping-methods/matching-cart`` is only usable once the cart has a shipping
        address: without one it fails with "does not have a shipping address set". The
        agent never collects an address -- that belongs to checkout -- so the country's own
        methods are the answer before then, and the precise per-cart match is used only
        when an address already happens to be on the cart.
        """
        cart = await self._find_cart(session)
        if cart:
            try:
                return await self._client.get_list(
                    "/shipping-methods/matching-cart", {"cartId": cart["id"]}
                )
            except CTError as error:
                logger.debug("matching-cart unavailable, using the country list: %s", error)
        return await self._client.get_list(
            "/shipping-methods", {"country": self._settings.country, "limit": 20}
        )

    @staticmethod
    def _shipping_fee(method: dict[str, Any]) -> tuple[float, str | None]:
        for zone_rate in method.get("zoneRates") or []:
            for rate in zone_rate.get("shippingRates") or []:
                fee = money(rate.get("price"))
                free_above = rate.get("freeAbove")
                if free_above:
                    return fee, f"free over {money(free_above)}"
                if rate.get("tiers"):
                    return fee, "price varies with cart value"
                return fee, None
        return 0.0, None
