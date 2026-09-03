"""``MerchantBackend`` over commercetools.

Reads come from products, inventory entries and orders. Writes are staged as proposals and
only ``apply_change`` touches the platform, translating a staged change into commercetools
update actions. Nothing here applies a change the approval surface has not marked approved:
that check lives in the executor and holds on every path.

What commercetools can and cannot supply, which is most of the design:

* **Sales, orders and average order value** are derived from Orders. Real numbers.
* **Traffic and conversion do not exist.** commercetools records no sessions, so both are
  ``None`` with a note rather than a stand-in zero, and both appear in the
  ``limitations`` list on the merchant context.
* **Unit cost and margin do not exist** as standard fields either, so ``min_price`` rests
  on a store rule (``min_price_basis="policy"``) and margin figures stay ``None``. A margin
  is never computed from an assumed cost.
* **Campaigns have no object at all**, so the config switches them off and the campaign
  methods raise rather than inventing a shape.
* **Alerts and issues are derived**: low stock from inventory entries, slow movers from
  30-day order lines, order issues from payment and shipment state.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from merchant_agent import (
    ActorKind,
    BusinessSnapshot,
    Campaign,
    CampaignDraft,
    ChangeItem,
    ChangeKind,
    ChangeStatus,
    InventoryActionItem,
    InventoryAlert,
    Listing,
    ListingDetails,
    ListingFilters,
    MerchantBackend,
    MerchantSessionContext,
    MetricPoint,
    MetricSeries,
    OrderIssue,
    PriceUpdateItem,
    PricingContext,
    PromotionDraft,
    StagedChange,
)
from merchant_agent.changes import (
    ChangeLedger,
    ChangeNotApplicable,
    GuardrailViolation,
    check_guardrails,
)
from merchant_agent.types import AlertCounts, DataLimitation

from ct_common import CTClient, CTError, ReferenceCache
from ct_common.mapping import (
    all_locales,
    derive_options,
    localized,
    money,
    parse_ref,
    select_price,
    swatch_label,
    variant_ref,
)

logger = logging.getLogger(__name__)

# Below this many units on hand, a variant is flagged low. commercetools carries no
# reorder point of its own on an inventory entry beyond `restockableInDays`, so this is a
# store rule and is described as one wherever it surfaces.
LOW_STOCK_THRESHOLD = 10
SLOW_MOVER_MAX_UNITS_30D = 1

TRAFFIC_NOTE = "commercetools records no sessions, so traffic and conversion are unavailable"


class CommercetoolsMerchant(MerchantBackend):
    def __init__(
        self,
        client: CTClient,
        *,
        config: Any,
        cache: ReferenceCache | None = None,
        ledger: Any = None,
    ) -> None:
        self._client = client
        self._settings = client.settings
        self._config = config
        self._cache = cache or ReferenceCache()
        # The in-process ledger is correct for one long-lived worker. A deployment with no
        # instance affinity passes CustomObjectChangeLedger instead, because staging and
        # approving are two different requests.
        self._ledger = ledger or ChangeLedger(config)

    # -- Shared helpers ------------------------------------------------------------------

    async def _attribute_types(self) -> dict[str, str]:
        async def load() -> dict[str, str]:
            payload = await self._client.get("/product-types", {"limit": 100})
            return {
                attribute["name"]: attribute["type"]["name"]
                for product_type in payload.get("results", [])
                for attribute in product_type.get("attributes", [])
            }

        return await self._cache.get("product-types", load)

    async def _orders_since(self, days: int) -> list[dict[str, Any]]:
        """Orders created in the last ``days``, newest first. The single source for every
        derived figure here, so it is fetched once per call site and passed around."""
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload = await self._client.get(
            "/orders",
            {"where": f'createdAt > "{since}"', "limit": 500, "sort": "createdAt desc"},
        )
        return payload.get("results") or []

    @staticmethod
    def _order_total(order: dict[str, Any]) -> float:
        """What the customer was charged, not the pre-tax cart figure."""
        taxed = ((order.get("taxedPrice") or {}).get("totalGross")) or order.get("totalPrice") or {}
        digits = taxed.get("fractionDigits", 2)
        return round((taxed.get("centAmount") or 0) / (10**digits), 2)

    def _list_price(self, variant: dict[str, Any]) -> float:
        """The catalogue's own price, before any Product Discount.

        This is what an operator edits and what the movement cap is checked against. The
        storefront quotes the discounted figure instead; showing a merchant that one means
        the number they reprice from disagrees with the number the cap is checked against,
        which is how a 5% increase ends up recorded against a base nobody saw.
        """
        return money(self._price_of(variant), effective=False)

    def _price_of(self, variant: dict[str, Any]) -> dict[str, Any] | None:
        """This deployment's own price for a variant, matched on currency and country."""
        return select_price(variant, self._settings.currency, self._settings.country)

    async def _product(self, product_id: str) -> dict[str, Any] | None:
        """The raw product, for its ``version`` and its content fields. Its prices are
        **not** authoritative for a write: see ``_selected_variants``."""
        payload = await self._client.get(f"/products/{product_id}")
        return payload or None

    async def _selected_variants(self, product_id: str) -> dict[int, dict[str, Any]]:
        """Variants as commercetools itself resolves them for this deployment's currency
        and country, keyed by variant id.

        This is the only trustworthy source for which price a write should target: the
        resolved ``price`` carries its own ``id``, and it is the price the storefront
        displays. A raw product read hands back every price a variant has -- six of them
        across three currencies here -- with no indication of which one selection picks.
        """
        payload = await self._client.get(
            "/product-projections/search",
            {
                "filter": f'id:"{product_id}"',
                "limit": 1,
                "staged": "true",
                **self._client.price_params(),
            },
        )
        results = payload.get("results") or []
        if not results:
            return {}
        projection = results[0]
        variants = [projection.get("masterVariant") or {}, *(projection.get("variants") or [])]
        return {v.get("id", 1): v for v in variants}

    # -- Performance ---------------------------------------------------------------------

    async def get_business_snapshot(
        self, session: MerchantSessionContext, period: str | None = None
    ) -> BusinessSnapshot:
        days = 30
        # Two independent windows; no reason to wait for one before asking for the other.
        window, previous_all = await asyncio.gather(
            self._orders_since(days), self._orders_since(days * 2)
        )
        cutoff = datetime.now(UTC) - timedelta(days=days)
        previous = [
            order
            for order in previous_all
            if datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00")) < cutoff
        ]

        sales = round(sum(self._order_total(order) for order in window), 2)
        prior_sales = round(sum(self._order_total(order) for order in previous), 2)
        orders = len(window)
        current_alerts, issues = await asyncio.gather(
            self.get_inventory_alerts(session), self.get_order_issues(session)
        )
        alerts = AlertCounts(
            low_stock=len([a for a in current_alerts if a.kind == "low_stock"]),
            slow_movers=len([a for a in current_alerts if a.kind == "slow_mover"]),
            order_issues=len(issues),
            pending_changes=len(self._ledger.pending()),
        )
        return BusinessSnapshot(
            period=period or f"last {days} days",
            compare_to=f"previous {days} days",
            sales=sales,
            orders=orders,
            # No sessions in commercetools: None, with the reason, never a zero.
            traffic=None,
            conversion_rate=None,
            average_order_value=round(sales / orders, 2) if orders else None,
            sales_change_pct=self._change_pct(sales, prior_sales),
            orders_change_pct=self._change_pct(orders, len(previous)),
            traffic_change_pct=None,
            conversion_change_pct=None,
            currency=self._settings.currency,
            alerts=alerts,
            note=TRAFFIC_NOTE,
        )

    @staticmethod
    def _change_pct(current: float, prior: float) -> float | None:
        if not prior:
            return None
        return round((current - prior) / prior * 100, 1)

    async def query_metrics(
        self,
        session: MerchantSessionContext,
        metric: str,
        period: str | None = None,
        granularity: str = "day",
        segment: str | None = None,
    ) -> MetricSeries:
        name = metric.strip().lower()
        if name in {"traffic", "sessions", "visits", "conversion", "conversion_rate"}:
            # Empty with a note, which is what the contract asks for an unavailable metric.
            return MetricSeries(metric=metric, granularity="day", period=period, note=TRAFFIC_NOTE)
        if name not in {"sales", "revenue", "orders", "aov", "average_order_value"}:
            return MetricSeries(
                metric=metric,
                granularity="day",
                period=period,
                note="derived from orders only; this metric has no commercetools source",
            )

        orders = await self._orders_since(30)
        buckets: dict[str, list[float]] = defaultdict(list)
        for order in orders:
            if segment and not self._order_in_segment(order, segment):
                continue
            day = order["createdAt"][:10]
            buckets[day].append(self._order_total(order))

        points = []
        for day in sorted(buckets):
            totals = buckets[day]
            if name in {"orders"}:
                value = float(len(totals))
            elif name in {"aov", "average_order_value"}:
                value = round(sum(totals) / len(totals), 2)
            else:
                value = round(sum(totals), 2)
            points.append(MetricPoint(date=day, value=value))

        return MetricSeries(
            metric=metric,
            unit=None if name == "orders" else self._settings.currency,
            granularity="day",
            period=period or "last 30 days",
            segment=segment,
            points=points,
            note="derived from orders; commercetools is the only source",
        )

    @staticmethod
    def _order_in_segment(order: dict[str, Any], segment: str) -> bool:
        needle = segment.strip().lower()
        for line in order.get("lineItems") or []:
            name = " ".join(str(v) for v in (line.get("name") or {}).values()).lower()
            if needle in name:
                return True
        return False

    async def get_campaign_performance(
        self, session: MerchantSessionContext, campaign_id: str | None = None
    ) -> list[Campaign]:
        # A system the business does not have. The config switch removes the tool on every
        # path; this raise exists so nothing calls it by accident.
        raise NotImplementedError(
            "commercetools has no campaign object; enable_campaigns is off for this deployment"
        )

    # -- Catalog -------------------------------------------------------------------------

    async def _to_listing(
        self, projection: dict[str, Any], types: dict[str, str], stock_by_sku: dict[str, int]
    ) -> Listing:
        master = projection.get("masterVariant") or {}
        variants = [master, *(projection.get("variants") or [])]
        options = derive_options(variants, types, self._settings.locale)
        published = projection.get("published", True)

        def variant_stock(variant: dict[str, Any]) -> int:
            return stock_by_sku.get(variant.get("sku") or "", 0)

        if options:
            # A family's portal price is its lowest variant's, in stock or not, so the
            # catalogue does not appear to move when a size sells out. Its stock is the sum.
            prices = [self._list_price(v) for v in variants]
            price = min((p for p in prices if p), default=0.0)
            stock = sum(variant_stock(v) for v in variants)
            listing_id = projection["id"]
        else:
            price = self._list_price(master)
            stock = variant_stock(master)
            listing_id = variant_ref(projection["id"], master.get("id", 1))

        status = "active" if published else "paused"
        if published and stock == 0:
            status = "out_of_stock"
        return Listing(
            listing_id=listing_id,
            title=all_locales(
                projection.get("nameAllLocales") or projection.get("name"), self._settings.locale
            ),
            status=status,
            price=price,
            currency=self._settings.currency,
            stock=stock,
            category=self._category_of(projection),
            attributes={},
            image_url=(master.get("images") or [{}])[0].get("url"),
            short_description=localized(projection.get("description"), self._settings.locale)[:300]
            or None,
            options=options,
        )

    def _category_of(self, projection: dict[str, Any]) -> str | None:
        for reference in projection.get("categories") or []:
            obj = reference.get("obj") or {}
            name = localized(obj.get("name"), self._settings.locale)
            if name:
                return name
        return None

    async def _inventory_by_sku(self, skus: list[str]) -> dict[str, dict[str, int]]:
        """Per sku: what the shop can sell, and what sits in other channels.

        A sku carries one InventoryEntry per supply channel, and this project has up to five
        per sku with very different quantities. Which one matters depends on what the shop
        draws on, and this storefront creates carts with **no** supply channel -- so the
        channel-less entry is the sellable figure, and it is also the one the shopping
        agent reads (``availability.noChannel``). Both agents therefore quote the same
        number.

        The other channels are not noise, though: 95 units in a warehouse is the reason not
        to reorder. They are returned alongside so a restock proposal can say so.
        """
        if not skus:
            return {}
        quoted = ", ".join(f'"{sku}"' for sku in skus[:400] if sku)
        if not quoted:
            return {}
        payload = await self._client.get(
            "/inventory", {"where": f"sku in ({quoted})", "limit": 500}
        )
        out: dict[str, dict[str, int]] = {}
        for entry in payload.get("results") or []:
            row = out.setdefault(entry["sku"], {"sellable": 0, "other_channels": 0, "seen": 0})
            quantity = entry.get("availableQuantity") or 0
            if entry.get("supplyChannel"):
                row["other_channels"] += quantity
            else:
                row["sellable"] += quantity
                row["seen"] = 1
        # A sku with no channel-less entry at all: the shop has nothing of its own to draw
        # on, so the channel total is the best available reading rather than a flat zero.
        for row in out.values():
            if not row["seen"]:
                row["sellable"] = row["other_channels"]
                row["other_channels"] = 0
        return out

    async def _stock_by_sku(self, skus: list[str]) -> dict[str, int]:
        """The sellable quantity per sku."""
        return {sku: row["sellable"] for sku, row in (await self._inventory_by_sku(skus)).items()}

    async def search_listings(
        self,
        session: MerchantSessionContext,
        query: str,
        filters: ListingFilters | None = None,
        limit: int = 8,
    ) -> list[Listing]:
        params = {
            "limit": min(max(limit * 2, limit), 24),
            "staged": "true",
            "expand": "categories[*]",
            **self._client.price_params(),
        }
        if query:
            params["text.en-US"] = query
        payload = await self._client.get("/product-projections/search", params)
        types = await self._attribute_types()
        projections = payload.get("results") or []
        skus = [
            v.get("sku")
            for p in projections
            for v in [p.get("masterVariant") or {}, *(p.get("variants") or [])]
            if v.get("sku")
        ]
        stock = await self._stock_by_sku(skus)
        listings = [await self._to_listing(p, types, stock) for p in projections]
        return self._filter_listings(listings, filters)[:limit]

    @staticmethod
    def _filter_listings(listings: list[Listing], filters: ListingFilters | None) -> list[Listing]:
        if filters is None:
            return listings
        kept = []
        for listing in listings:
            if filters.status and listing.status != filters.status:
                continue
            if (
                filters.category
                and filters.category.lower() not in (listing.category or "").lower()
            ):
                continue
            if filters.max_stock is not None and listing.stock > filters.max_stock:
                continue
            if filters.content_quality and listing.content_quality != filters.content_quality:
                continue
            kept.append(listing)
        if filters.sort == "stock_asc":
            kept.sort(key=lambda listing: listing.stock)
        elif filters.sort == "price_desc":
            kept.sort(key=lambda listing: listing.price, reverse=True)
        elif filters.sort == "price_asc":
            kept.sort(key=lambda listing: listing.price)
        return kept

    async def get_listing(
        self, session: MerchantSessionContext, listing_id: str
    ) -> ListingDetails | None:
        base_id, variant_id = parse_ref(listing_id)
        product = await self._product(base_id)
        if not product:
            return None
        current = (product.get("masterData") or {}).get("staged") or (
            product.get("masterData") or {}
        ).get("current")
        if not current:
            return None
        projection = {
            **current,
            "id": product["id"],
            "published": (product.get("masterData") or {}).get("published", True),
        }
        types = await self._attribute_types()
        variants = [projection.get("masterVariant") or {}, *(projection.get("variants") or [])]
        stock = await self._stock_by_sku([v.get("sku") for v in variants if v.get("sku")])
        options = derive_options(variants, types, self._settings.locale)

        if variant_id is not None and options:
            chosen = next((v for v in variants if v.get("id") == variant_id), variants[0])
            base = await self._to_variant_listing(projection, chosen, options, stock)
            return ListingDetails(**base.model_dump(), long_description=self._long(projection))
        family = await self._to_listing(projection, types, stock)
        rows = (
            [await self._to_variant_listing(projection, v, options, stock) for v in variants]
            if options
            else []
        )
        return ListingDetails(
            **family.model_dump(),
            long_description=self._long(projection),
            missing_attributes=self._missing_attributes(projection),
            variants=rows,
        )

    def _long(self, projection: dict[str, Any]) -> str | None:
        return localized(projection.get("description"), self._settings.locale) or None

    def _missing_attributes(self, projection: dict[str, Any]) -> list[str]:
        """Content gaps an operator can act on, from what the record itself lacks."""
        master = projection.get("masterVariant") or {}
        missing = []
        if not localized(projection.get("description"), self._settings.locale):
            missing.append("description")
        if not (master.get("images") or []):
            missing.append("image")
        if not master.get("sku"):
            missing.append("sku")
        return missing

    async def _to_variant_listing(
        self,
        projection: dict[str, Any],
        variant: dict[str, Any],
        options: dict[str, list[str]],
        stock: dict[str, int],
    ) -> Listing:
        attributes = {a["name"]: a.get("value") for a in variant.get("attributes") or []}
        option_values = {
            name: swatch_label(localized(attributes.get(name), self._settings.locale))
            for name in options
        }
        units = stock.get(variant.get("sku") or "", 0)
        return Listing(
            listing_id=variant_ref(projection["id"], variant.get("id", 1)),
            title=all_locales(
                projection.get("nameAllLocales") or projection.get("name"), self._settings.locale
            ),
            status="active" if units else "out_of_stock",
            price=self._list_price(variant),
            currency=self._settings.currency,
            stock=units,
            option_values={k: v for k, v in option_values.items() if v},
            variant_of=projection["id"],
            image_url=(variant.get("images") or [{}])[0].get("url"),
        )

    # -- Inventory and order health ------------------------------------------------------

    async def get_inventory_alerts(self, session: MerchantSessionContext) -> list[InventoryAlert]:
        """Derived, since commercetools has no alert object. Low stock comes from inventory
        entries against a store rule; slow movers from 30 days of order lines. Only the
        kinds that can be computed are returned."""

        async def load() -> list[InventoryAlert]:
            # The query finds *candidate* skus: entries below the threshold. It cannot be
            # the answer on its own, because a sku carries one entry per supply channel and
            # this project's data has five per sku with very different quantities -- one
            # channel holding 3 units of a sku with 146 in total. Aggregating only the
            # matching entries flagged that sku as low at "3", which reads to the operator
            # as a product to restock when it is fully stocked. So each candidate sku's
            # total across every channel is read next, and only that total is judged.
            candidates = await self._client.get(
                "/inventory",
                {"where": f"availableQuantity < {LOW_STOCK_THRESHOLD}", "limit": 100},
            )
            skus = sorted({row["sku"] for row in candidates.get("results") or [] if row.get("sku")})
            inventory = await self._inventory_by_sku(skus)

            sold: Counter[str] = Counter()
            for order in await self._orders_since(30):
                for line in order.get("lineItems") or []:
                    sku = (line.get("variant") or {}).get("sku")
                    if sku:
                        sold[sku] += line.get("quantity") or 0

            # Only the genuinely short skus need a listing resolved, and those lookups are
            # independent -- so they run together. Sequentially this was one round trip per
            # flagged sku, in a row, and the largest single cost in a digest turn.
            short = [
                (sku, (inventory.get(sku) or {"sellable": 0})["sellable"])
                for sku in skus
                if (inventory.get(sku) or {"sellable": 0})["sellable"] < LOW_STOCK_THRESHOLD
            ]
            resolved = await asyncio.gather(
                *(self._listing_for_sku(sku, units) for sku, units in short)
            )

            alerts: list[InventoryAlert] = []
            for (sku, units), listing in zip(short, resolved, strict=True):
                if listing is None:
                    continue
                units_30d = sold.get(sku, 0)
                alerts.append(
                    InventoryAlert(
                        listing_id=listing.listing_id,
                        title=listing.title,
                        kind="low_stock",
                        option_values=listing.option_values,
                        variant_of=listing.variant_of,
                        stock=units,
                        threshold=LOW_STOCK_THRESHOLD,
                        days_of_cover=round(units / (units_30d / 30), 1) if units_30d else None,
                        sales_last_30d=units_30d,
                        storefront_visible=listing.status == "active",
                    )
                )
                if units_30d <= SLOW_MOVER_MAX_UNITS_30D and units > 0:
                    alerts.append(
                        InventoryAlert(
                            listing_id=listing.listing_id,
                            title=listing.title,
                            kind="slow_mover",
                            option_values=listing.option_values,
                            variant_of=listing.variant_of,
                            stock=units,
                            sales_last_30d=units_30d,
                            storefront_visible=listing.status == "active",
                        )
                    )
            return alerts[:40]

        return await self._cache.get("inventory-alerts", load)

    async def _listing_for_sku(self, sku: str | None, units: int = 0) -> Listing | None:
        """The listing a sku belongs to, carrying that sku's real available quantity --
        passing a stub makes every derived alert report the listing as off the storefront."""
        if not sku:
            return None
        payload = await self._client.get(
            "/product-projections/search",
            {
                "filter": f'variants.sku:"{sku}"',
                "limit": 1,
                "staged": "true",
                **self._client.price_params(),
            },
        )
        results = payload.get("results") or []
        if not results:
            return None
        projection = results[0]
        types = await self._attribute_types()
        variants = [projection.get("masterVariant") or {}, *(projection.get("variants") or [])]
        options = derive_options(variants, types, self._settings.locale)
        variant = next((v for v in variants if v.get("sku") == sku), variants[0])
        stock = {sku: units}
        if options:
            return await self._to_variant_listing(projection, variant, options, stock)
        return await self._to_listing(projection, types, stock)

    async def _other_channel_stock(self, listing_id: str) -> int:
        """Units of this listing held in supply channels the storefront does not sell from."""
        product_id, variant_id = parse_ref(listing_id)
        selected = await self._selected_variants(product_id)
        variant = selected.get(variant_id or next(iter(selected), 1)) or {}
        sku = variant.get("sku")
        if not sku:
            return 0
        rows = await self._inventory_by_sku([sku])
        return (rows.get(sku) or {}).get("other_channels", 0)

    async def get_order_issues(self, session: MerchantSessionContext) -> list[OrderIssue]:
        """Derived from payment and shipment state, the only exception signals
        commercetools carries."""
        issues: list[OrderIssue] = []
        for order in await self._orders_since(30):
            payment_state = order.get("paymentState")
            shipment_state = order.get("shipmentState")
            kind: str | None = None
            summary = ""
            if payment_state in {"Failed", "CreditOwed"}:
                kind, summary = "damaged", f"payment state is {payment_state}"
            elif shipment_state in {"Delayed", "Backorder"}:
                kind, summary = "delayed", f"shipment state is {shipment_state}"
            elif order.get("orderState") == "Cancelled":
                kind, summary = "return_spike", "order was cancelled"
            if kind is None:
                continue
            issues.append(
                OrderIssue(
                    issue_id=f"issue-{order['id'][:8]}",
                    order_id=order.get("orderNumber") or order["id"],
                    kind=kind,  # type: ignore[arg-type]
                    summary=summary,
                    opened_at=datetime.fromisoformat(order["createdAt"].replace("Z", "+00:00")),
                )
            )
        return issues[:40]

    # -- Pricing -------------------------------------------------------------------------

    async def get_pricing_context(
        self, session: MerchantSessionContext, listing_id: str
    ) -> PricingContext | None:
        base_id, variant_id = parse_ref(listing_id)
        selected = await self._selected_variants(base_id)
        if not selected:
            return None
        variants = list(selected.values())
        types = await self._attribute_types()
        options = derive_options(variants, types, self._settings.locale)

        def context_for(variant: dict[str, Any]) -> PricingContext:
            price = self._list_price(variant)
            attributes = {a["name"]: a.get("value") for a in variant.get("attributes") or []}
            return PricingContext(
                listing_id=variant_ref(base_id, variant.get("id", 1)),
                current_price=price,
                currency=self._settings.currency,
                # commercetools carries no unit cost, so margin cannot be computed and is
                # never inferred from an assumed cost. The floor is a store rule.
                unit_cost=None,
                margin_pct=None,
                min_price=round(price * 0.5, 2) if price else None,
                max_price=round(price * 1.5, 2) if price else None,
                min_price_basis="policy",
                max_price_delta_pct=self._config.max_price_delta_pct,
                max_promotion_discount_pct=self._config.max_promotion_discount_pct,
                option_values={
                    name: swatch_label(localized(attributes.get(name), self._settings.locale))
                    for name in options
                },
            )

        if variant_id is not None:
            chosen = next((v for v in variants if v.get("id") == variant_id), None)
            return context_for(chosen) if chosen else None
        if not options:
            return context_for(variants[0])
        family = context_for(min(variants, key=lambda v: self._list_price(v) or float("inf")))
        return family.model_copy(
            update={"listing_id": base_id, "variants": [context_for(v) for v in variants]}
        )

    # -- Staged writes -------------------------------------------------------------------

    async def stage_listing_update(
        self,
        session: MerchantSessionContext,
        listing_id: str,
        fields: dict[str, Any],
        note: str | None = None,
    ) -> StagedChange:
        details = await self.get_listing(session, listing_id)
        if details is None:
            raise ChangeNotApplicable(f"no listing with id {listing_id!r}")
        supported = {"title", "description"}
        unsupported = set(fields) - supported
        if unsupported:
            raise ChangeNotApplicable(
                f"this catalogue stores {', '.join(sorted(unsupported))} on the product type, "
                "not as editable listing content"
            )
        before = {"title": details.title, "description": details.long_description}
        items = [
            ChangeItem(target=listing_id, field=name, before=before.get(name), after=value)
            for name, value in fields.items()
        ]
        return self._ledger.stage(
            kind=ChangeKind.LISTING_UPDATE,
            summary=note or f"Edit {', '.join(fields)} on {details.title}",
            items=items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
        )

    async def stage_price_update(
        self,
        session: MerchantSessionContext,
        items: list[PriceUpdateItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = []
        for item in items:
            context = await self.get_pricing_context(session, item.listing_id)
            if context is None:
                raise ChangeNotApplicable(f"no listing with id {item.listing_id!r}")
            change_items.append(
                ChangeItem(
                    target=item.listing_id,
                    field="price",
                    before=context.current_price,
                    after=item.new_price,
                )
            )
        return self._ledger.stage(
            kind=ChangeKind.PRICE_UPDATE,
            summary=note or f"Reprice {len(items)} item(s)",
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=self._settings.currency,
        )

    async def stage_inventory_action(
        self,
        session: MerchantSessionContext,
        items: list[InventoryActionItem],
        note: str | None = None,
    ) -> StagedChange:
        change_items = []
        notes: list[str] = []
        for item in items:
            listing = await self.get_listing(session, item.listing_id)
            if listing is None:
                raise ChangeNotApplicable(f"no listing with id {item.listing_id!r}")
            if item.action == "restock":
                change_items.append(
                    ChangeItem(
                        target=item.listing_id,
                        field="stock",
                        before=listing.stock,
                        after=listing.stock + (item.quantity or 0),
                    )
                )
                elsewhere = await self._other_channel_stock(item.listing_id)
                if elsewhere:
                    # The operator should see this before approving: reordering when the
                    # stock already exists a channel away is the wrong call.
                    notes.append(
                        f"{item.listing_id} has {elsewhere} unit(s) in other supply "
                        "channels, which this storefront does not sell from"
                    )
            else:
                change_items.append(
                    ChangeItem(
                        target=item.listing_id,
                        field="status",
                        before=listing.status,
                        after="paused" if item.action == "pause" else "active",
                    )
                )
        return self._ledger.stage(
            kind=ChangeKind.INVENTORY_ACTION,
            summary=note or f"Inventory action on {len(items)} item(s)",
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            guardrail_notes=notes,
        )

    async def stage_promotion(
        self, session: MerchantSessionContext, promotion: PromotionDraft
    ) -> StagedChange:
        change_items = []
        for listing_id in promotion.listing_ids:
            context = await self.get_pricing_context(session, listing_id)
            if context is None:
                raise ChangeNotApplicable(f"no listing with id {listing_id!r}")
            targets = context.variants or [context]
            for target in targets:
                after = round(target.current_price * (1 - promotion.discount_pct / 100), 2)
                change_items.append(
                    ChangeItem(
                        target=target.listing_id,
                        field="price",
                        before=target.current_price,
                        after=after,
                    )
                )
        return self._ledger.stage(
            kind=ChangeKind.PROMOTION,
            summary=(
                f"{promotion.name}: {promotion.discount_pct:g}% off {len(change_items)} item(s)"
            ),
            items=change_items,
            actor=session.operator,
            actor_kind=ActorKind.AGENT,
            currency=self._settings.currency,
        )

    async def stage_campaign(
        self, session: MerchantSessionContext, campaign: CampaignDraft
    ) -> StagedChange:
        raise ChangeNotApplicable(
            "commercetools has no campaign system, so campaigns are not managed here"
        )

    async def get_pending_changes(self, session: MerchantSessionContext) -> list[StagedChange]:
        return self._ledger.pending()

    async def apply_change(self, session: MerchantSessionContext, change_id: str) -> StagedChange:
        """The platform write. The ledger refuses anything not currently staged, the
        commercetools actions run, and only then is the change marked applied -- so a failed
        write leaves it staged rather than recording a change the store cannot see."""
        change = self._ledger.get(change_id)
        if change is None:
            raise ChangeNotApplicable(f"no change with id {change_id!r} to apply")
        # Both checks run BEFORE the write, not after. The ledger repeats them when it
        # marks the change applied, but by then the platform write has already happened --
        # so applying an already-applied change would write a second time and only then
        # raise, charging a price move or a restock twice.
        if change.status is not ChangeStatus.STAGED:
            raise ChangeNotApplicable(
                f"change {change_id} is {change.status.value}, not staged — nothing to apply"
            )
        violations = check_guardrails(change.kind, change.items, self._config)
        if violations:
            raise GuardrailViolation(violations)
        await self._write(change)
        self._cache.invalidate()
        return self._ledger.apply(change_id, session.operator)

    async def discard_change(
        self,
        session: MerchantSessionContext,
        change_id: str,
        actor_kind: ActorKind = ActorKind.OPERATOR,
    ) -> StagedChange:
        return self._ledger.discard(change_id, session.operator, actor_kind)

    # -- The commercetools writes --------------------------------------------------------

    async def _write(self, change: StagedChange) -> None:
        if change.kind is ChangeKind.LISTING_UPDATE:
            await self._write_listing(change)
        elif change.kind is ChangeKind.PRICE_UPDATE:
            await self._write_prices(change)
        elif change.kind is ChangeKind.INVENTORY_ACTION:
            await self._write_inventory(change)
        elif change.kind is ChangeKind.PROMOTION:
            await self._write_prices(change)
        else:
            raise ChangeNotApplicable(f"{change.kind.value} is not applied by this backend")

    async def _write_listing(self, change: StagedChange) -> None:
        by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in change.items:
            product_id, _ = parse_ref(item.target)
            locale = self._settings.locale
            if item.field == "title":
                by_product[product_id].append(
                    {"action": "changeName", "name": {locale: str(item.after)}, "staged": False}
                )
            elif item.field == "description":
                by_product[product_id].append(
                    {
                        "action": "setDescription",
                        "description": {locale: str(item.after)},
                        "staged": False,
                    }
                )
        for product_id, actions in by_product.items():
            await self._update_product(product_id, actions)

    async def _write_prices(self, change: StagedChange) -> None:
        by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in change.items:
            product_id, variant_id = parse_ref(item.target)
            selected = await self._selected_variants(product_id)
            if not selected:
                raise ChangeNotApplicable(f"listing {item.target} no longer exists")
            variant = selected.get(variant_id or next(iter(selected))) or next(
                iter(selected.values())
            )
            # The price commercetools itself resolved, so the write lands on the one the
            # storefront displays rather than on an arbitrary entry of the price list.
            price = self._price_of(variant)
            if not price or not price.get("id"):
                raise ChangeNotApplicable(
                    f"{item.target} has no {self._settings.currency} price to change"
                )
            cents = int(round(float(item.after) * 100))
            by_product[product_id].append(
                {
                    "action": "changePrice",
                    "priceId": price["id"],
                    "price": {
                        "value": {
                            "currencyCode": self._settings.currency,
                            "centAmount": cents,
                        }
                    },
                    "staged": False,
                }
            )
        for product_id, actions in by_product.items():
            await self._update_product(product_id, actions)

    async def _write_inventory(self, change: StagedChange) -> None:
        for item in change.items:
            product_id, variant_id = parse_ref(item.target)
            if item.field == "status":
                action = "unpublish" if item.after == "paused" else "publish"
                await self._update_product(product_id, [{"action": action}])
                continue
            # A restock: raise the sku's inventory entry by the difference.
            product = await self._product(product_id)
            staged = ((product or {}).get("masterData") or {}).get("staged") or {}
            variants = [staged.get("masterVariant") or {}, *(staged.get("variants") or [])]
            variant = next(
                (v for v in variants if v.get("id") == (variant_id or v.get("id"))), variants[0]
            )
            sku = variant.get("sku")
            if not sku:
                raise ChangeNotApplicable(f"{item.target} has no sku, so stock cannot be set")
            added = int(item.after) - int(item.before)
            if added <= 0:
                continue
            entries = await self._client.get("/inventory", {"where": f'sku="{sku}"', "limit": 1})
            results = entries.get("results") or []
            if results:
                entry = results[0]
                await self._client.post(
                    f"/inventory/{entry['id']}",
                    {
                        "version": entry["version"],
                        "actions": [{"action": "addQuantity", "quantity": added}],
                    },
                )
            else:
                # No entry means the sku was untracked; creating one starts tracking it.
                await self._client.post("/inventory", {"sku": sku, "quantityOnStock": added})

    async def _update_product(self, product_id: str, actions: list[dict[str, Any]]) -> None:
        if not actions:
            return
        product = await self._product(product_id)
        if not product:
            raise ChangeNotApplicable(f"product {product_id} no longer exists")
        try:
            await self._client.post(
                f"/products/{product_id}", {"version": product["version"], "actions": actions}
            )
        except CTError as error:
            logger.warning("product %s write failed: %s", product_id, error)
            raise

    # -- Merchant context ----------------------------------------------------------------

    async def get_merchant_context(self, session: MerchantSessionContext) -> dict[str, Any] | None:
        """Small, because it is sent on every request. The limitations are the honest part:
        what this store's systems cannot supply, so the assistant states the one that bears
        on an answer instead of reporting a zero."""
        return {
            "store": self._settings.project_key,
            "currency": self._settings.currency,
            "reporting_period": "last 30 days",
            "limitations": [
                DataLimitation(
                    source="analytics",
                    note=(
                        "commercetools records no sessions, so traffic and "
                        "conversion are unavailable"
                    ),
                ).model_dump(),
                DataLimitation(
                    source="cost",
                    note=(
                        "no unit cost is stored, so margin cannot be computed and "
                        "price floors are a store rule"
                    ),
                ).model_dump(),
                DataLimitation(
                    source="campaigns",
                    note=(
                        "commercetools has no campaign object; campaign management is switched off"
                    ),
                ).model_dump(),
            ],
        }

    # -- Host reads (portal widgets; no agent tool reaches these) -------------------------

    async def recent_orders(self, limit: int = 8) -> list[dict[str, Any]]:
        """The order feed the portal's home page shows, newest first."""
        orders = await self._orders_since(30)
        rows = []
        for order in orders[:limit]:
            rows.append(
                {
                    "order_id": order.get("orderNumber") or order["id"],
                    "status": order.get("orderState") or "Open",
                    "placed_at": order["createdAt"],
                    "total": self._order_total(order),
                    "items": sum(
                        line.get("quantity") or 0 for line in order.get("lineItems") or []
                    ),
                }
            )
        return rows

    async def resolved_changes(self) -> list[StagedChange]:
        """Applied and discarded changes, newest first, for the portal's audit view."""
        return sorted(self._ledger.resolved(), key=lambda change: change.created_at, reverse=True)

    async def kpi_trends(self, session: MerchantSessionContext) -> dict[str, list[dict[str, Any]]]:
        """Sparkline series for the portal's KPI tiles. Only the metrics commercetools can
        actually supply appear here -- traffic and conversion are absent rather than flat
        lines at zero."""
        trends: dict[str, list[dict[str, Any]]] = {}
        for metric in ("sales", "orders"):
            series = await self.query_metrics(session, metric)
            trends[metric] = [point.model_dump(mode="json") for point in series.points]
        return trends
