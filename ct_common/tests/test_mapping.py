"""The mapper's rules, each pinned to what the live project actually contains."""

from __future__ import annotations

from ct_common.mapping import (
    derive_options,
    localized,
    money,
    parse_ref,
    select_price,
    strip_test_prefix,
    swatch_label,
    to_product,
    to_product_details,
    variant_ref,
)

TYPES = {
    "color": "ltext",
    "finish": "ltext",
    "size": "ltext",
    "diameter-in-inches": "number",
    "new-arrival": "boolean",
    "promotion-id": "set",
    "taxjarProductTaxCode": "text",
    "productspec": "ltext",
}


def variant(vid: int, cents: int, **attributes: object) -> dict:
    return {
        "id": vid,
        "price": {"value": {"centAmount": cents, "currencyCode": "USD", "fractionDigits": 2}},
        "images": [{"url": f"https://example.test/{vid}.jpg"}],
        "attributes": [{"name": k, "value": v} for k, v in attributes.items()],
    }


def projection(*variants: dict, name: str = "Test Chair") -> dict:
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "name": {"en-US": name},
        "description": {"en-US": "A chair."},
        "masterVariant": variants[0],
        "variants": list(variants[1:]),
    }


def test_money_respects_fraction_digits():
    assert money({"value": {"centAmount": 2999, "fractionDigits": 2}}) == 29.99
    # A zero-decimal currency must not come out a hundred times high.
    assert money({"value": {"centAmount": 2999, "fractionDigits": 0}}) == 2999.0
    assert money(None) == 0.0


def test_localized_handles_strings_sets_and_scalars():
    assert localized({"en-US": "Sofa", "de-DE": "Sofa DE"}, "en-US") == "Sofa"
    assert localized({"de-DE": "Nur Deutsch"}, "en-US") == "Nur Deutsch"  # falls back
    assert localized([{"en-US": "a"}, {"en-US": "b"}], "en-US") == "a, b"
    assert localized(True, "en-US") == "yes"


def test_swatch_label_drops_the_hex_code():
    assert swatch_label("Light Pink:#FFB6C1") == "Light Pink"
    assert swatch_label("Gold") == "Gold"
    # A colon that is not a hex code is left alone.
    assert swatch_label("Set: two chairs") == "Set: two chairs"


def test_variant_reference_round_trip():
    assert parse_ref(variant_ref("abc", 4)) == ("abc", 4)
    # A bare id is a family, which the cart must refuse.
    assert parse_ref("abc") == ("abc", None)


def test_identical_variants_are_a_plain_product():
    """85 of 100 products in this project have seven or more variants that are identical on
    every attribute. Presenting one as a family invents choices that do not exist."""
    same = [variant(i, 499, color="Brown:#a52a2a", taxjarProductTaxCode="99000") for i in (1, 2, 3)]
    product = to_product(projection(*same), TYPES, "en-US", "USD")
    assert product.options == {}
    assert product.product_id == variant_ref("11111111-2222-3333-4444-555555555555", 1)


def test_differing_attribute_becomes_an_option():
    product = to_product(
        projection(
            variant(1, 15000, color="White:#fff"), variant(2, 4999, color="Golden Rod:#daa")
        ),
        TYPES,
        "en-US",
        "USD",
    )
    assert product.options == {"color": ["White", "Golden Rod"]}
    # A family's storefront price is its lowest in-stock variant's.
    assert product.price == 49.99


def test_boolean_and_set_attributes_are_never_options():
    """``new-arrival`` and ``promotion-id`` both vary across this catalogue's variants and
    neither is a choice a shopper makes."""
    options = derive_options(
        [
            variant(1, 100, **{"new-arrival": True, "promotion-id": ["A"]}),
            variant(2, 100, **{"new-arrival": False, "promotion-id": ["B"]}),
        ],
        TYPES,
        "en-US",
    )
    assert options == {}


def test_operational_fields_are_never_options():
    options = derive_options(
        [variant(1, 100, taxjarProductTaxCode="A"), variant(2, 100, taxjarProductTaxCode="B")],
        TYPES,
        "en-US",
    )
    assert options == {}


def test_duplicate_option_combinations_collapse_to_the_cheapest():
    """A live family carries eight variants covering two colours, three of them identical
    "White" records at two prices."""
    details = to_product_details(
        projection(
            variant(1, 15000, color="White:#fff"),
            variant(2, 4999, color="Golden Rod:#daa"),
            variant(3, 12000, color="White:#fff"),
            variant(4, 12000, color="White:#fff"),
        ),
        TYPES,
        "en-US",
        "USD",
    )
    assert len(details.variants) == 2
    by_colour = {v.option_values["color"]: v.price for v in details.variants}
    assert by_colour == {"White": 120.0, "Golden Rod": 49.99}


def test_missing_availability_is_purchasable_not_out_of_stock():
    """Only 10 of 100 products carry availability past the master variant. No InventoryEntry
    means inventory is not tracked, so reading absence as zero would empty the catalogue."""
    product = to_product(projection(variant(1, 100)), TYPES, "en-US", "USD")
    assert product.in_stock is True


def test_zero_available_quantity_is_out_of_stock():
    out = variant(1, 100)
    out["availability"] = {"availableQuantity": 0, "isOnStock": False}
    assert to_product(projection(out), TYPES, "en-US", "USD").in_stock is False


def test_variant_details_resolve_to_that_variant():
    details = to_product_details(
        projection(
            variant(1, 15000, color="White:#fff"), variant(2, 4999, color="Golden Rod:#daa")
        ),
        TYPES,
        "en-US",
        "USD",
        variant_id=2,
    )
    assert details.price == 49.99
    assert details.option_values == {"color": "Golden Rod"}
    assert details.variant_of == "11111111-2222-3333-4444-555555555555"


def discounted_variant(vid: int, cents: int, discounted_cents: int, **attributes: object) -> dict:
    v = variant(vid, cents, **attributes)
    v["price"]["discounted"] = {
        "value": {
            "centAmount": discounted_cents,
            "currencyCode": "USD",
            "fractionDigits": 2,
        }
    }
    return v


def test_an_applied_product_discount_is_the_price_quoted():
    """62 of this project's 159 products carry a discounted price. Reading the list price
    would have the agent misquote more than a third of the catalogue."""
    product = to_product(projection(discounted_variant(1, 129900, 110415)), TYPES, "en-US", "USD")
    assert product.price == 1104.15
    assert "sale" in product.labels
    # The pre-discount price is not smuggled into attributes: they render as keyless chips.
    assert "was" not in product.attributes


def test_no_discount_means_no_sale_label_and_no_was_line():
    product = to_product(projection(variant(1, 2999)), TYPES, "en-US", "USD")
    assert product.price == 29.99
    assert product.labels == []


def test_new_arrival_flag_and_category_both_label_new():
    by_flag = to_product(
        projection(variant(1, 100, **{"new-arrival": True})), TYPES, "en-US", "USD"
    )
    assert "new" in by_flag.labels
    with_category = projection(variant(1, 100))
    with_category["categories"] = [{"name": "New Arrivals"}]
    assert "new" in to_product(with_category, TYPES, "en-US", "USD").labels


def test_a_familys_from_price_is_the_cheapest_effective_price():
    """The lowest in-stock variant's price after its own discount, not before it."""
    product = to_product(
        projection(
            variant(1, 15000, color="White:#fff"),
            discounted_variant(2, 14000, 9000, color="Golden Rod:#daa"),
        ),
        TYPES,
        "en-US",
        "USD",
    )
    assert product.options == {"color": ["White", "Golden Rod"]}
    assert product.price == 90.0


def test_swatch_label_handles_this_catalogues_three_shapes():
    assert swatch_label("Light Pink:#FFB6C1") == "Light Pink"
    # The suffix is sometimes the colour word again rather than a hex code.
    assert swatch_label("Transparent:transparent") == "Transparent"
    assert swatch_label("Lightslate Gray:lightslategray") == "Lightslate Gray"
    # A colon that is neither a swatch nor a repeat is left alone.
    assert swatch_label("Set: two chairs") == "Set: two chairs"
    assert swatch_label("Gold") == "Gold"


def multi_currency_variant() -> dict:
    """The real shape of a raw product read on this project: six prices, three currencies,
    two of them USD, and no price selection to resolve them."""

    def price(pid, cents, currency, country=None):
        entry = {
            "id": pid,
            "value": {"centAmount": cents, "currencyCode": currency, "fractionDigits": 2},
        }
        if country:
            entry["country"] = country
        return entry

    return {
        "id": 1,
        "prices": [
            price("eur-1", 4000, "EUR"),
            price("gbp-1", 3100, "GBP"),
            price("usd-scoped", 3100, "USD", country="CA"),
            price("usd-plain", 2767, "USD"),
        ],
        "attributes": [],
    }


def test_select_price_matches_the_currency_not_the_first_entry():
    """Repricing the wrong currency while reporting the right one is worse than failing."""
    chosen = select_price(multi_currency_variant(), "USD")
    assert chosen["id"] == "usd-plain"
    assert money(chosen) == 27.67


def test_select_price_prefers_an_unscoped_price_over_a_country_scoped_one():
    """Not the "sensible" preference, but the one the platform actually makes: for a
    variant carrying both an unscoped USD price and a country-scoped USD price at different
    amounts, commercetools' own selection resolved to the unscoped one. Guessing the other
    way means a write landing on a price the shop never displays."""
    assert select_price(multi_currency_variant(), "USD", "CA")["id"] == "usd-plain"
    assert select_price(multi_currency_variant(), "USD", "US")["id"] == "usd-plain"


def test_select_price_uses_a_country_scoped_price_only_when_nothing_is_unscoped():
    variant = multi_currency_variant()
    variant["prices"] = [p for p in variant["prices"] if p["id"] != "usd-plain"]
    assert select_price(variant, "USD", "CA")["id"] == "usd-scoped"


def test_select_price_returns_none_when_the_currency_is_absent():
    assert select_price(multi_currency_variant(), "JPY") is None


def test_a_resolved_price_from_price_selection_wins_outright():
    variant = multi_currency_variant()
    variant["price"] = {"value": {"centAmount": 999, "currencyCode": "USD", "fractionDigits": 2}}
    assert money(select_price(variant, "USD")) == 9.99


def test_money_separates_what_a_shopper_pays_from_what_the_catalogue_says():
    """A merchant edits the list price; a shopper is quoted the discounted one. Conflating
    them records a price move against a base the operator never saw."""
    price = {
        "value": {"centAmount": 2767, "currencyCode": "USD", "fractionDigits": 2},
        "discounted": {"value": {"centAmount": 2352, "currencyCode": "USD", "fractionDigits": 2}},
    }
    assert money(price) == 23.52
    assert money(price, effective=False) == 27.67


def test_the_projects_test_prefix_is_stripped_from_display_values():
    """One family in this shared project carries "KMB"-tagged values from someone's earlier
    test run. Offering a shopper "KMB Gold" as a finish is worse than showing nothing."""
    assert swatch_label("KMB Gold:#FFD700") == "Gold"
    assert swatch_label("KMB Lavender Blush:#fff0f5") == "Lavender Blush"
    assert strip_test_prefix("KMB- Dry clean only") == "Dry clean only"
    assert swatch_label("Gold:#FFD700") == "Gold"
