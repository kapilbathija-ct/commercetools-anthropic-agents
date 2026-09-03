"""The merchant agent's deployment config.

Three of the five systems the merchant agent knows about exist in commercetools; the other
two do not, and are switched off rather than left to fail:

* **Campaigns** have no commercetools object at all, so ``enable_campaigns`` is off. That
  removes the campaign tools, prompt lines and grounding rule on every path, and
  ``marketing-campaigns`` is parked under ``skills/_staged/``.
* **The analysis delegate** needs SQL. commercetools is not a warehouse, so it stays off
  until ``CT_ANALYSIS_BQ_DATASET`` names the read-only view that order events reach through
  Subscriptions -> Pub/Sub -> BigQuery.

Listing edits, inventory and pricing are all real: products, inventory entries and
embedded prices respectively.
"""

from __future__ import annotations

import os

from merchant_agent import MerchantAgentConfig


def build_merchant_config(**overrides: object) -> MerchantAgentConfig:
    analysis_dataset = os.environ.get("CT_ANALYSIS_BQ_DATASET") or None
    return MerchantAgentConfig(
        brand_name="Hi Kapil",
        # The reference defaults the merchant to Opus. A morning-digest turn there runs a
        # skill load plus four reads and took 18-24 seconds end to end, which is a long
        # silence in front of an audience. Sonnet answers the same turns in well under
        # half that with no loss on this workload -- these are reads and arithmetic over
        # small tables, not deep reasoning. MERCHANT_MODEL overrides it.
        model=os.environ.get("MERCHANT_MODEL", "claude-sonnet-5"),
        approval_surface="the Approve button on the change preview card",
        # Every write is a staged change the host applies. apply_change succeeds only for a
        # change the approval surface marked approved, whatever is typed in chat.
        require_host_approval=True,
        # Systems commercetools has.
        enable_listing_edits=True,
        enable_inventory=True,
        enable_pricing=True,
        # Systems it does not.
        enable_campaigns=False,
        enable_analysis=bool(analysis_dataset),
        analysis_sql_only=True,
        # The hosted code-execution sandbox is first-party API only; leave it off until the
        # warehouse view exists and the delegate is actually in use.
        analysis_use_code_execution=False,
        # commercetools prices under `price`; nothing in this project prices under another
        # field name, so the default price-bearing set holds. `taxCategory` joins the
        # protected set because changing it silently breaks cart creation for the product.
        protected_fields=(
            "listing_id",
            "currency",
            "tax_category",
            "taxCategory",
            "compliance_notes",
        ),
        metrics_intent_terms=(
            *MerchantAgentConfig.model_fields["metrics_intent_terms"].default,
            "gmv",
            "sell through",
            "stock cover",
        ),
        **overrides,
    )
