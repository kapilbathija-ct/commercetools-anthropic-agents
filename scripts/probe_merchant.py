"""Exercise every MerchantBackend read against the live project, and stage a write.

Read-only apart from the staging, which touches nothing: a staged change is a proposal in
this process's ledger until ``apply_change`` runs, and this script never applies one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from merchant_agent import (  # noqa: E402
    InventoryActionItem,
    ListingFilters,
    MerchantSessionContext,
    PriceUpdateItem,
)

from ct_common import CTClient, load_settings  # noqa: E402
from ct_merchant import CommercetoolsMerchant, build_merchant_config  # noqa: E402


async def main() -> None:
    settings = load_settings()
    client = CTClient(settings)
    config = build_merchant_config()
    backend = CommercetoolsMerchant(client, config=config)
    session = MerchantSessionContext(
        session_id="probe-merchant", merchant_id=settings.project_key, operator="probe@acme.test"
    )
    try:
        print("== get_business_snapshot ==")
        snap = await backend.get_business_snapshot(session)
        print(
            f"  period={snap.period} sales=${snap.sales} orders={snap.orders} aov={snap.average_order_value}"
        )
        print(f"  traffic={snap.traffic} conversion={snap.conversion_rate}  note={snap.note}")
        print(f"  change: sales {snap.sales_change_pct}% orders {snap.orders_change_pct}%")
        print(f"  alerts: {snap.alerts.model_dump()}")

        print("\n== query_metrics('sales') ==")
        series = await backend.query_metrics(session, "sales")
        print(f"  {len(series.points)} points, unit={series.unit}, note={series.note}")
        for point in series.points[:5]:
            print(f"    {point.date}  {point.value}")

        print("\n== query_metrics('traffic') must be empty with a note ==")
        traffic = await backend.query_metrics(session, "traffic")
        print(f"  points={len(traffic.points)}  note={traffic.note}")

        print("\n== search_listings('rug', stock_asc) ==")
        listings = await backend.search_listings(
            session, "rug", ListingFilters(sort="stock_asc"), limit=5
        )
        for listing in listings:
            print(
                f"  {listing.title:<28} {listing.status:<12} ${listing.price:<8} stock={listing.stock:<5} {listing.listing_id}"
            )

        print("\n== get_listing on the first result ==")
        details = await backend.get_listing(session, listings[0].listing_id)
        print(
            f"  {details.title}  variants={len(details.variants)}  missing={details.missing_attributes}"
        )

        print("\n== get_pricing_context ==")
        context = await backend.get_pricing_context(session, listings[0].listing_id)
        print(
            f"  current=${context.current_price}  floor=${context.min_price} basis={context.min_price_basis}"
        )
        print(
            f"  unit_cost={context.unit_cost} margin={context.margin_pct} (neither is stored in commercetools)"
        )

        print("\n== get_inventory_alerts ==")
        alerts = await backend.get_inventory_alerts(session)
        for alert in alerts[:6]:
            print(
                f"  {alert.kind:<11} {alert.title:<26} stock={alert.stock:<4} sold30d={alert.sales_last_30d} visible={alert.storefront_visible}"
            )
        print(f"  {len(alerts)} alert(s) total")

        print("\n== get_order_issues ==")
        issues = await backend.get_order_issues(session)
        for issue in issues[:5]:
            print(f"  {issue.kind:<13} order {issue.order_id}  {issue.summary}")
        print(f"  {len(issues)} issue(s) total")

        print("\n== stage_price_update (a proposal; nothing is written) ==")
        target = listings[0].listing_id
        staged = await backend.stage_price_update(
            session,
            [PriceUpdateItem(listing_id=target, new_price=round(context.current_price * 1.05, 2))],
        )
        print(f"  {staged.change_id} {staged.status}: {staged.summary}")
        for item in staged.items:
            print(f"    {item.field} {item.before} -> {item.after} on {item.target}")

        print("\n== a price move past the cap must be refused ==")
        try:
            await backend.stage_price_update(
                session, [PriceUpdateItem(listing_id=target, new_price=context.current_price * 3)]
            )
            print("  ERROR: the guardrail did not fire")
        except Exception as error:
            print(f"  {type(error).__name__}: {str(error)[:160]}")

        print("\n== stage_inventory_action past the restock cap must be refused ==")
        try:
            await backend.stage_inventory_action(
                session, [InventoryActionItem(listing_id=target, action="restock", quantity=99_999)]
            )
            print("  ERROR: the guardrail did not fire")
        except Exception as error:
            print(f"  {type(error).__name__}: {str(error)[:160]}")

        print("\n== get_pending_changes ==")
        print(f"  {len(await backend.get_pending_changes(session))} staged")

        print("\n== campaigns are switched off for this deployment ==")
        try:
            await backend.get_campaign_performance(session)
            print("  ERROR: should have raised")
        except NotImplementedError as error:
            print(f"  NotImplementedError: {error}")

        print("\n== get_merchant_context ==")
        ctx = await backend.get_merchant_context(session)
        for limitation in ctx["limitations"]:
            print(f"  {limitation['source']}: {limitation['note']}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
