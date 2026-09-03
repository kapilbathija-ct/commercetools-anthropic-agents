"""commercetools projections into the agents' records.

**Ids.** A commercetools variant has no global id of its own, only an integer that is
unique within its product, so a purchasable record is addressed here as
``{productId}#{variantId}``. A family is addressed by the product's own id. The two
namespaces cannot collide because a commercetools id never contains ``#``, which is what
``docs/backends.md`` asks for when a parent's id could equal a child's. Both are opaque
strings the model passes back unchanged.

**Options.** Which attributes are options is derived from the data, not from
``attributeConstraint``. On this project ``CombinationUnique`` appears on exactly one
Product Type (``lighting-and-decor``); the main catalogue's ``color``, ``finish`` and
``size`` are all ``None``, so a constraint-based rule would have declared a chair sold in
three finishes to be a plain product. The rule used instead is: an attribute is an option
when its value actually differs across the product's variants, its type can be displayed
as a choice, and it is not an operational field.

That rule also settles the more surprising half of this catalogue. Of 100 published
products sampled live, **85 have seven or more variants that are byte-identical on every
attribute** -- duplicate records, not choices -- and only 15 differ on anything. Those 85
map to plain products under their master variant's id. Presenting them as a family is not
a harmless cosmetic difference: it is the defect already recorded against the storefront,
where variant-selector links rendered with identical accessible names because the
underlying variants shared every attribute the UI checked.
"""

from __future__ import annotations

import json
from typing import Any

from shopping_agent import Product, ProductDetails

# Attribute types that can be shown as a choice. A boolean is a flag (``new-arrival``), a
# set is a collection (``promotion-id``, ``bundleContents``), and a reference is a link --
# none of them is an option a shopper picks, however much they vary across variants.
OPTION_ELIGIBLE_TYPES = frozenset({"text", "ltext", "enum", "lenum", "number"})

# Operational fields that vary but are not merchandising choices. ``taxjarProductTaxCode``
# drives the TaxJar connector; ``productspec`` and ``product-description`` are long-form
# copy; ``promotion-id`` is a campaign tag.
OPTION_DENYLIST = frozenset(
    {
        "taxjarProductTaxCode",
        "productspec",
        "product-description",
        "promotion-id",
        "new-arrival",
        "bundleContents",
        "type",
    }
)

VARIANT_SEPARATOR = "#"


def variant_ref(product_id: str, variant_id: int) -> str:
    return f"{product_id}{VARIANT_SEPARATOR}{variant_id}"


def parse_ref(reference: str) -> tuple[str, int | None]:
    """``{productId}#{variantId}`` into its parts; a bare id is a family."""
    if VARIANT_SEPARATOR not in reference:
        return reference, None
    product_id, _, variant = reference.partition(VARIANT_SEPARATOR)
    return product_id, int(variant) if variant.isdigit() else None


def localized(value: Any, locale: str, fallbacks: tuple[str, ...] = ("en-US", "en-GB")) -> str:
    """A LocalizedString, a set of them, or a plain scalar, as one display string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for candidate in (locale, *fallbacks):
            if value.get(candidate):
                return str(value[candidate])
        return str(next(iter(value.values()), ""))
    if isinstance(value, list):
        return ", ".join(localized(item, locale, fallbacks) for item in value if item is not None)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def all_locales(
    entries: Any, locale: str, fallbacks: tuple[str, ...] = ("en-US", "en-GB", "en")
) -> str:
    """A GraphQL ``*AllLocales`` list as one display string.

    Asked for a single locale, commercetools GraphQL returns null when the product has no
    value for exactly that key -- and this project holds products named under ``en``
    rather than ``en-US``, which rendered with no title at all. Preferring the session's
    locale, then the fallbacks, then whatever exists means a product always has a name.
    """
    if not entries:
        return ""
    if isinstance(entries, str):
        return entries
    # REST hands back a LocalizedString object where GraphQL hands back a list of
    # {locale, value}. Both reach these mappers, so both are accepted here.
    if isinstance(entries, dict):
        return localized(entries, locale, fallbacks)
    by_locale = {
        entry.get("locale"): entry.get("value")
        for entry in entries
        if isinstance(entry, dict) and entry.get("value")
    }
    for candidate in (locale, *fallbacks):
        if by_locale.get(candidate):
            return str(by_locale[candidate])
    # A language match before giving up: "en-GB" satisfies a request for "en".
    language = locale.split("-")[0]
    for key, value in by_locale.items():
        if key and key.split("-")[0] == language:
            return str(value)
    return str(next(iter(by_locale.values()), ""))


def select_price(
    variant: dict[str, Any], currency: str, country: str | None = None
) -> dict[str, Any] | None:
    """The price this deployment sells at, out of a variant's whole price list.

    **Prefer letting commercetools resolve this.** A read that went through price selection
    (``priceCurrency`` / ``priceCountry``) carries a single resolved ``price`` that also
    carries its own ``id`` -- which is what a ``changePrice`` action needs, and what makes
    the write land on the same price the storefront shows.

    The fallback below exists only for a raw product read, which has no price selection,
    and it is deliberately conservative. Modelling the platform's selection rules is a trap:
    a variant here carries both an unscoped USD price and a ``country: "US"`` USD price at
    different amounts, and commercetools' own selection for ``USD``/``US`` resolved to the
    **unscoped** one. A heuristic that "sensibly" preferred the country-scoped price
    therefore disagreed with the platform -- and for a write that means repricing a price
    the shop never displays while reporting the one it does.
    """
    if variant.get("price"):
        return variant["price"]
    prices = variant.get("prices") or []
    matching = [p for p in prices if (p.get("value") or {}).get("currencyCode") == currency]
    if not matching:
        return None
    # Unscoped first, because that is what selection actually chose here; a scoped price is
    # a last resort rather than a preference.
    unscoped = [
        p
        for p in matching
        if not p.get("country") and not p.get("customerGroup") and not p.get("channel")
    ]
    if unscoped:
        return unscoped[0]
    if country:
        exact = [p for p in matching if p.get("country") == country]
        if exact:
            return exact[0]
    return matching[0]


def money(price: dict[str, Any] | None, *, effective: bool = True) -> float:
    """A commercetools money value as a decimal amount.

    ``fractionDigits`` is respected rather than assumed to be two, so a zero-decimal
    currency does not come out a hundred times high.

    ``effective`` decides which number this is, and the two callers want different ones.
    A shopper is quoted what they will pay, so an active Product Discount wins over the
    list price -- 62 of this project's 159 products have one, and reading ``value`` alone
    misquotes more than a third of the catalogue. An **operator editing the catalogue** is
    working on the list price; the discount is a separate promotion they did not open. Show
    a merchant the discounted figure and the number they reprice from disagrees with the
    number the movement cap is checked against, which is how a 5% increase gets recorded
    against the wrong base.
    """
    if not price:
        return 0.0
    discounted = (price.get("discounted") or {}).get("value") if effective else None
    value = discounted or price.get("value") or price
    cents = value.get("centAmount")
    if cents is None:
        return 0.0
    digits = value.get("fractionDigits", 2)
    return round(cents / (10**digits), 2)


def list_price(price: dict[str, Any] | None) -> float | None:
    """The pre-discount price, or None when nothing is discounted."""
    if not price or not (price.get("discounted") or {}).get("value"):
        return None
    value = price.get("value")
    if not value:
        return None
    digits = value.get("fractionDigits", 2)
    return round(value["centAmount"] / (10**digits), 2)


def currency_of(price: dict[str, Any] | None, default: str) -> str:
    if not price:
        return default
    value = price.get("value") or price
    return value.get("currencyCode") or default


def swatch_label(value: str) -> str:
    """``"Light Pink:#FFB6C1"`` renders as ``Light Pink``.

    This catalogue appends a swatch to colour and finish values, usually a hex code but
    sometimes the colour word again (``"Transparent:transparent"``). Neither is worth
    showing a shopper or letting the model repeat back. A colon that is neither -- as in
    ``"Set: two chairs"`` -- is left alone.
    """
    if ":" not in value:
        return value
    head, _, tail = value.rpartition(":")
    head, tail = head.strip(), tail.strip()
    if not head:
        return value
    if tail.startswith("#") or tail.lower() == head.lower().replace(" ", ""):
        return head
    if tail.lower() == head.lower():
        return head
    return value


def _normalized(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _attribute_map(variant: dict[str, Any]) -> dict[str, Any]:
    return {a["name"]: a.get("value") for a in variant.get("attributes") or []}


def derive_options(
    variants: list[dict[str, Any]],
    attribute_types: dict[str, str],
    locale: str,
) -> dict[str, list[str]]:
    """The option names and their values in the order the variants list them.

    ``attribute_types`` maps an attribute name to its Product Type type name, so a set or a
    boolean is excluded whatever it does across the variants.
    """
    if len(variants) < 2:
        return {}
    maps = [_attribute_map(v) for v in variants]
    names = {name for m in maps for name in m}
    options: dict[str, list[str]] = {}
    for name in sorted(names):
        if name in OPTION_DENYLIST:
            continue
        if attribute_types.get(name) not in OPTION_ELIGIBLE_TYPES:
            continue
        if len({_normalized(m.get(name)) for m in maps}) < 2:
            continue
        values: list[str] = []
        for m in maps:
            label = swatch_label(localized(m.get(name), locale))
            if label and label not in values:
                values.append(label)
        if len(values) > 1:
            options[name] = values
    return options


def dedupe_variants(
    projection: dict[str, Any],
    variants: list[dict[str, Any]],
    options: dict[str, list[str]],
    locale: str,
    currency: str = "USD",
) -> list[dict[str, Any]]:
    """One variant per distinct combination of option values, cheapest in-stock first.

    This catalogue's families carry duplicates: a side table has eight variants covering
    two colours, three of them identical "White" records at two different prices. Only the
    variants listed exist, so listing the same choice several times tells the model there
    are distinctions it then has to invent -- and it is what made the storefront render
    variant links with identical accessible names. Collapsing here also keeps a family
    inside the fenced-result cap: the same side table drops from 8,873 to well under half
    that.
    """
    if not options:
        return variants

    def sort_key(variant: dict[str, Any]) -> tuple[int, float]:
        price = money(select_price(variant, currency))
        return (0 if _variant_stock(variant) else 1, price or float("inf"))

    chosen: dict[tuple[str, ...], dict[str, Any]] = {}
    for variant in sorted(variants, key=sort_key):
        attributes = _attribute_map(variant)
        key = tuple(
            swatch_label(localized(attributes.get(name), locale)) for name in sorted(options)
        )
        chosen.setdefault(key, variant)
    # Back into the catalogue's own order, so the first option value stays the first.
    order = {id(v): i for i, v in enumerate(variants)}
    return sorted(chosen.values(), key=lambda v: order[id(v)])


def _variant_stock(variant: dict[str, Any]) -> bool:
    """A missing ``availability`` means this variant has no InventoryEntry, which in
    commercetools means inventory is not tracked for it -- not that it is out of stock. On
    this project only 10 of 100 products carry any availability past the master variant, so
    reading absence as zero would empty the whole catalogue. Carts are created with the
    default inventory mode (no checks), so an untracked variant is purchasable.
    """
    availability = variant.get("availability")
    if not availability:
        return True
    if "availableQuantity" in availability:
        return (availability.get("availableQuantity") or 0) > 0
    return bool(availability.get("isOnStock", True))


def _image_url(variant: dict[str, Any]) -> str | None:
    images = variant.get("images") or []
    return images[0].get("url") if images else None


def _variant_attributes(variant: dict[str, Any], locale: str) -> dict[str, str]:
    """The attributes worth showing, as short display strings. Long-form copy and
    operational codes are left out; they belong in the description or nowhere."""
    out: dict[str, str] = {}
    for name, value in _attribute_map(variant).items():
        if name in OPTION_DENYLIST - {"type"}:
            continue
        rendered = swatch_label(localized(value, locale))
        if rendered and len(rendered) <= 60:
            out[name] = rendered
    return out


def _labels(
    projection: dict[str, Any], variant: dict[str, Any], price: dict[str, Any]
) -> list[str]:
    """Merchandising signals the storefront reads, each derived from a real platform fact:
    an applied Product Discount, and the catalogue's own new-arrival flag."""
    labels = []
    if (price.get("discounted") or {}).get("value"):
        labels.append("sale")
    attributes = _attribute_map(variant)
    if attributes.get("new-arrival") is True or any(
        (category.get("name") or "") == "New Arrivals"
        for category in projection.get("categories") or []
    ):
        labels.append("new")
    return labels


def _base_fields(
    projection: dict[str, Any],
    variant: dict[str, Any],
    locale: str,
    default_currency: str,
) -> dict[str, Any]:
    price = select_price(variant, default_currency) or {}
    # The pre-discount price is deliberately NOT put in ``attributes``: those render as
    # bare value chips with no key, so a lone "299.00" beside a "$254.15" price reads as
    # nonsense on the tile and is just as ambiguous in the model's fenced data. The "sale"
    # label carries the fact; a before-and-after price belongs in a presentation extension.
    attributes = _variant_attributes(variant, locale)
    return {
        "title": all_locales(projection.get("nameAllLocales") or projection.get("name"), locale),
        "price": money(price),
        "currency": currency_of(price, default_currency),
        "image_url": _image_url(variant) or _image_url(projection.get("masterVariant") or {}),
        "in_stock": _variant_stock(variant),
        "short_description": localized(projection.get("description"), locale)[:300] or None,
        "attributes": attributes,
        "labels": _labels(projection, variant, price),
    }


def to_product(
    projection: dict[str, Any],
    attribute_types: dict[str, str],
    locale: str,
    default_currency: str,
    category_name: str | None = None,
) -> Product:
    """One search result. A family carries its options and its lowest in-stock variant's
    price, as ``docs/backends.md`` specifies for a storefront; anything else is plain and
    is addressed by its master variant so a cart write is unambiguous."""
    product_id = projection["id"]
    master = projection.get("masterVariant") or {}
    variants = [master, *(projection.get("variants") or [])]
    options = derive_options(variants, attribute_types, locale)
    if options:
        listed = dedupe_variants(projection, variants, options, locale, default_currency)
        in_stock = [v for v in listed if _variant_stock(v)]
        cheapest = min(
            in_stock or listed,
            key=lambda v: money(select_price(v, default_currency)) or float("inf"),
        )
        fields = _base_fields(projection, cheapest, locale, default_currency)
        # A family is in stock while any variant is, whatever the cheapest one says.
        fields["in_stock"] = bool(in_stock)
        return Product(
            product_id=product_id,
            category=category_name,
            options=options,
            **fields,
        )
    return Product(
        product_id=variant_ref(product_id, master.get("id", 1)),
        category=category_name,
        **_base_fields(projection, master, locale, default_currency),
    )


def to_variant_product(
    projection: dict[str, Any],
    variant: dict[str, Any],
    options: dict[str, list[str]],
    locale: str,
    default_currency: str,
) -> Product:
    """A purchasable child inside its family's details: its own id, price and stock, one
    value per option, and the family's id."""
    attributes = _attribute_map(variant)
    option_values = {
        name: swatch_label(localized(attributes.get(name), locale)) for name in options
    }
    return Product(
        product_id=variant_ref(projection["id"], variant.get("id", 1)),
        option_values={k: v for k, v in option_values.items() if v},
        variant_of=projection["id"],
        **_base_fields(projection, variant, locale, default_currency),
    )


def to_product_details(
    projection: dict[str, Any],
    attribute_types: dict[str, str],
    locale: str,
    default_currency: str,
    variant_id: int | None = None,
    category_name: str | None = None,
) -> ProductDetails:
    """The full record for one id. A variant reference returns that variant; a family
    returns its variants inside the one result."""
    master = projection.get("masterVariant") or {}
    variants = [master, *(projection.get("variants") or [])]
    options = derive_options(variants, attribute_types, locale)
    long_description = localized(projection.get("description"), locale) or None
    specs = {}
    spec_text = localized(_attribute_map(master).get("productspec"), locale)
    if spec_text:
        for line in spec_text.splitlines():
            cleaned = line.lstrip("-* ").strip()
            if cleaned:
                specs[f"detail {len(specs) + 1}"] = cleaned[:120]

    if variant_id is not None and options:
        chosen = next((v for v in variants if v.get("id") == variant_id), master)
        base = to_variant_product(projection, chosen, options, locale, default_currency)
        return ProductDetails(
            **base.model_dump(), long_description=long_description, specs=specs, variants=[]
        )
    if not options:
        chosen = (
            next((v for v in variants if v.get("id") == variant_id), master)
            if variant_id
            else master
        )
        return ProductDetails(
            product_id=variant_ref(projection["id"], chosen.get("id", 1)),
            category=category_name,
            long_description=long_description,
            specs=specs,
            **_base_fields(projection, chosen, locale, default_currency),
        )
    family = to_product(projection, attribute_types, locale, default_currency, category_name)
    listed = dedupe_variants(projection, variants, options, locale, default_currency)
    return ProductDetails(
        **family.model_dump(),
        long_description=long_description,
        specs=specs,
        variants=[
            to_variant_product(projection, v, options, locale, default_currency) for v in listed
        ],
    )
