"""The GraphQL documents and the shape normalizers.

Field selection is the whole point. The REST Product Search API returns 144,902 bytes for
five products on this project (29 KB each, every locale and every variant); the same five
through the document below are 34,786 bytes, a 4.2x reduction measured live. Carts are
starker still -- REST has no locale projection and ignores ``filter[attributes]``, so a
cart on a wide Product Type runs to 103 KB against 2.6 KB here.

GraphQL names two things differently from REST, so everything is normalized into the REST
shape the mappers already read: ``attributesRaw`` becomes ``attributes``, and
``availability.noChannel`` is flattened to ``availability``.
"""

from __future__ import annotations

from typing import Any

_VARIANT_FRAGMENT = """
fragment SearchVariant on ProductSearchVariant {
  id
  sku
  price(currency: $currency, country: $country) {
    value { centAmount currencyCode fractionDigits }
  }
  images { url }
  availability { noChannel { availableQuantity isOnStock } }
  attributesRaw { name value }
}
"""


_CATALOG_VARIANT_FRAGMENT = """
fragment CatalogVariant on ProductVariant {
  id
  sku
  price(currency: $currency, country: $country) {
    value { centAmount currencyCode fractionDigits }
  }
  images { url }
  availability { noChannel { availableQuantity isOnStock } }
  attributesRaw { name value }
}
"""

SEARCH_QUERY = (
    """
query Search(
  $text: String, $locale: Locale!, $currency: Currency!, $country: Country!,
  $limit: Int!, $filters: [SearchFilterInput!], $sorts: [String!]
) {
  productProjectionSearch(
    text: $text, locale: $locale, staged: false, limit: $limit,
    filters: $filters, sorts: $sorts,
    priceSelector: { currency: $currency, country: $country }
  ) {
    total
    results {
      id
      name(locale: $locale)
      description(locale: $locale)
      categories { name(locale: $locale) }
      masterVariant { ...SearchVariant }
      variants { ...SearchVariant }
    }
  }
}
"""
    + _VARIANT_FRAGMENT
)

_ORDER_FRAGMENT = """
fragment OrderFields on Order {
  id
  orderNumber
  createdAt
  orderState
  shipmentState
  paymentState
  totalPrice { centAmount currencyCode fractionDigits }
  taxedPrice { totalGross { centAmount currencyCode fractionDigits } }
  lineItems {
    productId
    quantity
    name(locale: $locale)
    variant { id sku }
    price { value { centAmount currencyCode fractionDigits } }
  }
  shippingInfo { shippingMethodName }
}
"""

# ``taxedPrice.totalGross`` is what the customer was actually charged. An Order's
# ``totalPrice`` is the same pre-tax field a Cart carries, and reusing a cart summary
# mapper for an order is how an order-history page ends up quoting the wrong number.
ORDERS_QUERY = (
    """
query Orders($where: String!, $limit: Int!, $locale: Locale!) {
  orders(where: $where, limit: $limit, sort: "createdAt desc") {
    results { ...OrderFields }
  }
}
"""
    + _ORDER_FRAGMENT
)

ORDER_QUERY = (
    """
query OneOrder($where: String!, $locale: Locale!) {
  orders(where: $where, limit: 1) { results { ...OrderFields } }
}
"""
    + _ORDER_FRAGMENT
)


PRODUCT_QUERY = (
    """
query OneProduct($id: String!, $locale: Locale!, $currency: Currency!, $country: Country!) {
  product(id: $id) {
    id
    masterData {
      current {
        name(locale: $locale)
        description(locale: $locale)
        categories { name(locale: $locale) }
        masterVariant { ...CatalogVariant }
        variants { ...CatalogVariant }
      }
    }
  }
}
"""
    + _CATALOG_VARIANT_FRAGMENT
)

_CART_FIELDS = """
fragment CartFields on Cart {
  id
  version
  cartState
  totalPrice { centAmount currencyCode fractionDigits }
  lineItems {
    id
    productId
    quantity
    name(locale: $locale)
    variant { id sku images { url } attributesRaw { name value } }
    price { value { centAmount currencyCode fractionDigits } }
    totalPrice { centAmount currencyCode fractionDigits }
  }
}
"""

FIND_CART_QUERY = (
    """
query FindCart($where: String!, $locale: Locale!) {
  carts(where: $where, limit: 1, sort: "lastModifiedAt desc") {
    results { ...CartFields }
  }
}
"""
    + _CART_FIELDS
)

CART_BY_ID_QUERY = (
    """
query CartById($id: String!, $locale: Locale!) {
  cart(id: $id) { ...CartFields }
}
"""
    + _CART_FIELDS
)


def normalize_variant(variant: dict[str, Any] | None) -> dict[str, Any]:
    """A GraphQL variant into the REST shape the mappers read."""
    if not variant:
        return {}
    out = dict(variant)
    if "attributesRaw" in out:
        out["attributes"] = out.pop("attributesRaw") or []
    availability = out.get("availability")
    if isinstance(availability, dict) and "noChannel" in availability:
        out["availability"] = availability.get("noChannel")
    return out


def normalize_projection(projection: dict[str, Any]) -> dict[str, Any]:
    """A GraphQL product projection into the REST shape, categories included."""
    out = dict(projection)
    out["masterVariant"] = normalize_variant(projection.get("masterVariant"))
    out["variants"] = [normalize_variant(v) for v in projection.get("variants") or []]
    return out


def category_label(projection: dict[str, Any]) -> str | None:
    """The most specific category name the projection carries."""
    names = [c.get("name") for c in projection.get("categories") or [] if c.get("name")]
    return names[0] if names else None
