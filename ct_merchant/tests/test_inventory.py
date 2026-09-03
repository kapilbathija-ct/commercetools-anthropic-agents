"""How stock is counted, which decides whether an alert is worth an operator's time."""

from __future__ import annotations

import pytest
from merchant_agent import MerchantSessionContext

from ct_merchant import build_merchant_config
from ct_merchant.backend import CommercetoolsMerchant

SESSION = MerchantSessionContext(session_id="s", merchant_id="m", operator="op@test")


class InventoryClient:
    """Returns inventory entries only, in the multi-channel shape this project holds."""

    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries
        self.settings = type(
            "S",
            (),
            {"currency": "USD", "country": "US", "locale": "en-US", "project_key": "p"},
        )()

    def price_params(self) -> dict:
        return {}

    async def get(self, path: str, params: dict | None = None) -> dict:
        if path.startswith("/inventory"):
            return {"results": self._entries}
        return {"results": []}

    async def get_list(self, path: str, params: dict | None = None) -> list:
        return []

    async def post(self, path: str, body: dict) -> dict:
        return {}


def entry(sku: str, quantity: int, channel: str | None = None) -> dict:
    row = {"id": f"{sku}-{channel or 'none'}", "sku": sku, "availableQuantity": quantity}
    if channel:
        row["supplyChannel"] = {"typeId": "channel", "id": channel}
    return row


def backend(entries: list[dict]) -> CommercetoolsMerchant:
    return CommercetoolsMerchant(InventoryClient(entries), config=build_merchant_config())


@pytest.mark.asyncio
async def test_sellable_stock_is_the_channel_less_entry():
    """This storefront creates carts with no supply channel, so the channel-less entry is
    what it can sell -- and it is the same figure the shopping agent reads. A warehouse
    channel holding 114 units does not make the shop's 1 unit healthy."""
    rows = await backend(
        [entry("SKU-1", 1), entry("SKU-1", 100, "warehouse"), entry("SKU-1", 14, "store")]
    )._inventory_by_sku(["SKU-1"])
    assert rows["SKU-1"]["sellable"] == 1
    assert rows["SKU-1"]["other_channels"] == 114


@pytest.mark.asyncio
async def test_several_channel_less_entries_are_summed():
    rows = await backend([entry("SKU-1", 3), entry("SKU-1", 4)])._inventory_by_sku(["SKU-1"])
    assert rows["SKU-1"]["sellable"] == 7


@pytest.mark.asyncio
async def test_with_no_channel_less_entry_the_channel_total_is_the_reading():
    """Otherwise a sku stocked only through channels reads as a flat zero, which is a
    fabricated shortage rather than an unknown."""
    rows = await backend(
        [entry("SKU-2", 40, "warehouse"), entry("SKU-2", 2, "store")]
    )._inventory_by_sku(["SKU-2"])
    assert rows["SKU-2"]["sellable"] == 42
    assert rows["SKU-2"]["other_channels"] == 0


@pytest.mark.asyncio
async def test_a_negative_quantity_is_reported_as_it_stands():
    """Oversold stock is real state in this project; clamping it to zero would hide it."""
    rows = await backend([entry("SKU-3", -5)])._inventory_by_sku(["SKU-3"])
    assert rows["SKU-3"]["sellable"] == -5


@pytest.mark.asyncio
async def test_stock_by_sku_exposes_only_the_sellable_figure():
    stock = await backend([entry("SKU-1", 2), entry("SKU-1", 99, "warehouse")])._stock_by_sku(
        ["SKU-1"]
    )
    assert stock == {"SKU-1": 2}


@pytest.mark.asyncio
async def test_no_skus_means_no_inventory_calls_worth_making():
    assert await backend([])._inventory_by_sku([]) == {}
