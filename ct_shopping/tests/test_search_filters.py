"""How filters are split between the platform and this process."""

from __future__ import annotations

from shopping_agent import SearchFilters

from ct_shopping.backend import CommercetoolsStorefront as Store


def test_a_price_range_becomes_a_platform_filter_in_minor_units():
    built = Store._price_filter(SearchFilters(min_price=20.0, max_price=60.0))
    ranges = built[0]["model"]["range"]
    assert ranges["path"] == "variants.price.centAmount"
    assert ranges["ranges"] == [{"from": "2000", "to": "6000"}]


def test_an_open_ended_range_still_sends_both_bounds():
    """commercetools rejects a half-open range outright ("Reason: ranges[0].from"), so an
    open end travels as an explicit sentinel rather than an omitted key."""
    ceiling = Store._price_filter(SearchFilters(max_price=99.99))[0]["model"]["range"]["ranges"][0]
    assert ceiling == {"from": "0", "to": "9999"}
    floor = Store._price_filter(SearchFilters(min_price=5.0))[0]["model"]["range"]["ranges"][0]
    assert floor["from"] == "500"
    assert int(floor["to"]) > 500


def test_no_price_bounds_means_no_platform_filter():
    assert Store._price_filter(None) is None
    assert Store._price_filter(SearchFilters(category="Rugs")) is None
