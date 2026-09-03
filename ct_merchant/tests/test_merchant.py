"""The merchant agent's own contract, offline.

What is worth pinning here is the difference between a proposal and a write, and the price
the write targets — the two places where a mistake is expensive rather than merely wrong.
"""

from __future__ import annotations

import pytest
from merchant_agent import (
    ChangeKind,
    ChangeStatus,
    InventoryActionItem,
    MerchantSessionContext,
    PriceUpdateItem,
)
from merchant_agent.changes import ChangeNotApplicable, GuardrailViolation

from ct_common.mapping import money, select_price
from ct_merchant import build_merchant_config
from ct_merchant.backend import CommercetoolsMerchant

SESSION = MerchantSessionContext(
    session_id="s-1", merchant_id="test-project", operator="operator@test"
)


class FakeClient:
    """Just enough commercetools to exercise staging and the write translation, recording
    every request so a test can assert on what would have been sent."""

    def __init__(self, product: dict) -> None:
        self._product = product
        self.posts: list[tuple[str, dict]] = []
        self.settings = type(
            "S",
            (),
            {"currency": "USD", "country": "US", "locale": "en-US", "project_key": "test-project"},
        )()

    def price_params(self) -> dict[str, str]:
        return {"priceCurrency": "USD", "priceCountry": "US", "localeProjection": "en-US"}

    async def get(self, path: str, params: dict | None = None) -> dict:
        if path.startswith("/product-types"):
            return {"results": []}
        if path.startswith("/product-projections/search"):
            return {"results": [self._projection()]}
        if path.startswith("/products/"):
            return self._product
        if path.startswith("/orders") or path.startswith("/inventory"):
            return {"results": []}
        return {}

    async def get_list(self, path: str, params: dict | None = None) -> list:
        return []

    async def post(self, path: str, body: dict) -> dict:
        self.posts.append((path, body))
        return {"id": "p-1", "version": body.get("version", 1) + 1}

    def _projection(self) -> dict:
        data = self._product["masterData"]["staged"]
        variant = dict(data["masterVariant"])
        # What price selection hands back: one resolved price, carrying its own id.
        variant["price"] = {
            "id": "usd-unscoped",
            "value": {"centAmount": 2767, "currencyCode": "USD", "fractionDigits": 2},
        }
        return {
            "id": self._product["id"],
            "nameAllLocales": data["nameAllLocales"],
            "masterVariant": variant,
            "variants": [],
        }


def build_product() -> dict:
    def price(pid, cents, currency, country=None):
        entry = {
            "id": pid,
            "value": {"centAmount": cents, "currencyCode": currency, "fractionDigits": 2},
        }
        if country:
            entry["country"] = country
        return entry

    return {
        "id": "prod-1",
        "version": 7,
        "masterData": {
            "published": True,
            "staged": {
                "nameAllLocales": [{"locale": "en-US", "value": "Test Rug"}],
                "description": {"en-US": "A rug."},
                "masterVariant": {
                    "id": 1,
                    "sku": "RUG-1",
                    "attributes": [],
                    "images": [{"url": "https://example.test/r.jpg"}],
                    "prices": [
                        price("usd-unscoped", 2767, "USD"),
                        price("usd-us", 3100, "USD", country="US"),
                        price("eur", 4000, "EUR", country="DE"),
                    ],
                },
                "variants": [],
            },
        },
    }


def build_backend() -> tuple[CommercetoolsMerchant, FakeClient]:
    client = FakeClient(build_product())
    return CommercetoolsMerchant(client, config=build_merchant_config()), client


@pytest.mark.asyncio
async def test_staging_writes_nothing():
    """A staged change is a proposal. Nothing may reach the platform until it is applied."""
    backend, client = build_backend()
    change = await backend.stage_price_update(
        SESSION, [PriceUpdateItem(listing_id="prod-1#1", new_price=29.00)]
    )
    assert change.status is ChangeStatus.STAGED
    assert change.kind is ChangeKind.PRICE_UPDATE
    assert client.posts == [], "staging must not write to commercetools"


@pytest.mark.asyncio
async def test_applying_targets_the_price_the_platform_resolves():
    """The variant carries three prices, two of them USD. The write must land on the one
    price selection returns -- repricing another one changes a price the shop never shows
    while reporting the one it does."""
    backend, client = build_backend()
    change = await backend.stage_price_update(
        SESSION, [PriceUpdateItem(listing_id="prod-1#1", new_price=29.00)]
    )
    await backend.apply_change(SESSION, change.change_id)
    assert len(client.posts) == 1
    path, body = client.posts[0]
    assert path == "/products/prod-1"
    action = body["actions"][0]
    assert action["action"] == "changePrice"
    assert action["priceId"] == "usd-unscoped"
    assert action["price"]["value"]["centAmount"] == 2900
    # The stored version travels with the write, so a concurrent edit is refused.
    assert body["version"] == 7


@pytest.mark.asyncio
async def test_a_price_move_past_the_cap_is_refused_at_staging():
    backend, _ = build_backend()
    with pytest.raises(GuardrailViolation) as raised:
        await backend.stage_price_update(
            SESSION, [PriceUpdateItem(listing_id="prod-1#1", new_price=99.00)]
        )
    assert "exceeds" in str(raised.value)


@pytest.mark.asyncio
async def test_a_restock_past_the_cap_is_refused_at_staging():
    backend, _ = build_backend()
    with pytest.raises(GuardrailViolation):
        await backend.stage_inventory_action(
            SESSION, [InventoryActionItem(listing_id="prod-1#1", action="restock", quantity=10_000)]
        )


@pytest.mark.asyncio
async def test_applying_an_unknown_change_is_refused_and_writes_nothing():
    backend, client = build_backend()
    with pytest.raises(ChangeNotApplicable):
        await backend.apply_change(SESSION, "chg-9999")
    assert client.posts == []


@pytest.mark.asyncio
async def test_applying_twice_is_refused():
    """The second call must not write again."""
    backend, client = build_backend()
    change = await backend.stage_price_update(
        SESSION, [PriceUpdateItem(listing_id="prod-1#1", new_price=29.00)]
    )
    await backend.apply_change(SESSION, change.change_id)
    with pytest.raises(ChangeNotApplicable):
        await backend.apply_change(SESSION, change.change_id)
    assert len(client.posts) == 1


@pytest.mark.asyncio
async def test_a_discarded_change_cannot_be_applied():
    backend, client = build_backend()
    change = await backend.stage_price_update(
        SESSION, [PriceUpdateItem(listing_id="prod-1#1", new_price=29.00)]
    )
    await backend.discard_change(SESSION, change.change_id)
    with pytest.raises(ChangeNotApplicable):
        await backend.apply_change(SESSION, change.change_id)
    assert client.posts == []


@pytest.mark.asyncio
async def test_campaigns_refuse_rather_than_invent_a_shape():
    backend, _ = build_backend()
    with pytest.raises(NotImplementedError):
        await backend.get_campaign_performance(SESSION)


@pytest.mark.asyncio
async def test_a_listing_edit_refuses_fields_this_catalogue_cannot_store():
    backend, _ = build_backend()
    with pytest.raises(ChangeNotApplicable) as raised:
        await backend.stage_listing_update(SESSION, "prod-1#1", {"colour": "blue"})
    assert "colour" in str(raised.value)


@pytest.mark.asyncio
async def test_the_snapshot_reports_none_for_what_commercetools_cannot_supply():
    backend, _ = build_backend()
    snapshot = await backend.get_business_snapshot(SESSION)
    assert snapshot.traffic is None
    assert snapshot.conversion_rate is None
    assert snapshot.note and "no sessions" in snapshot.note


def test_the_merchant_reads_the_list_price_not_the_discounted_one():
    """An operator edits the catalogue price; the cap is checked against it too."""
    price = {
        "value": {"centAmount": 2767, "currencyCode": "USD", "fractionDigits": 2},
        "discounted": {"value": {"centAmount": 2352, "currencyCode": "USD", "fractionDigits": 2}},
    }
    assert money(price, effective=False) == 27.67
    assert money(price) == 23.52


def test_price_selection_prefers_what_the_platform_prefers():
    variant = {
        "prices": [
            {"id": "usd-us", "value": {"centAmount": 3100, "currencyCode": "USD"}, "country": "US"},
            {"id": "usd-plain", "value": {"centAmount": 2767, "currencyCode": "USD"}},
        ]
    }
    assert select_price(variant, "USD", "US")["id"] == "usd-plain"
