"""The merchant half of the service, mounted under ``/api/merchant``.

Same process as the storefront, so an approved change shows up in the shop at once.

The approval gate is the point of this module. ``require_host_approval`` is on, so
``apply_change`` succeeds only for a change id the host has marked approved — whatever is
typed in chat cannot approve anything. A click on the preview card is that mark, and it is
set immediately before the executor runs and cleared immediately after, whatever the
outcome, so a later chat turn cannot spend a leftover approval.

The operator identity is held server-side. A session id proves only that the caller started
a portal session; no request body names an operator or a merchant.
"""

# Route parameters are annotated with dependencies built at call time, so this module
# evaluates its annotations eagerly (no ``from __future__ import annotations``).

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from merchant_agent import MerchantSessionContext, MerchantSessionState
from pydantic import BaseModel, Field

from .host import append_user_turn, stream_turn
from .memory import install_memory_routes
from .sessions import session_dependency

logger = logging.getLogger(__name__)


class MerchantChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


def build_merchant_router(
    *,
    backend: Any,
    agent: Any,
    sessions: Any,
    executor_class: Any,
    merchant_id: str,
    operator: str,
    env_hint: str,
) -> APIRouter:
    router = APIRouter(prefix="/api/merchant")
    CurrentSession = session_dependency(sessions, "/api/merchant/session")

    def context(record: Any) -> MerchantSessionContext:
        """The operator and the merchant come from server-side config, not the request."""
        return MerchantSessionContext(
            session_id=record.session_id, merchant_id=merchant_id, operator=operator
        )

    @router.post("/session")
    async def start_session() -> dict:
        record = sessions.start(operator)
        return {"session_id": record.session_id, "operator": operator, "merchant": merchant_id}

    @router.post("/chat")
    async def chat(request: MerchantChatRequest, record: CurrentSession) -> StreamingResponse:
        append_user_turn(record, request.message, "Portal events")
        return stream_turn(agent, sessions, record, context(record), env_hint=env_hint)

    @router.get("/overview")
    async def overview(record: CurrentSession) -> dict:
        """The portal home page's whole data plane, in the shape the app expects. Traffic
        and conversion are absent from `trends` rather than drawn as flat zero lines: the
        limitations list says why, and the tiles read that."""
        session = context(record)
        snapshot = await backend.get_business_snapshot(session)
        alerts = await backend.get_inventory_alerts(session)
        issues = await backend.get_order_issues(session)
        pending = await backend.get_pending_changes(session)
        resolved = await backend.resolved_changes()
        # What the host resolved and put in front of the authenticated operator becomes
        # provenance for this session, the same way a host-side product read does on the
        # storefront. Without this the portal renders Approve and Dismiss for a change the
        # gate then refuses, because the change queue is process-wide while `seen_changes`
        # is per session -- so a change staged in an earlier session is visible and
        # unactionable. The gate exists to stop the *model* naming a change id it never
        # saw; it is not meant to stop the store's own queue being worked. Approval is
        # still required to apply: that mark is set only by a click, for that click.
        record.state.seen_changes.update({change.change_id: change for change in pending})
        return {
            "snapshot": snapshot.model_dump(mode="json"),
            "needs_attention": {
                "inventory": [alert.model_dump(mode="json") for alert in alerts[:12]],
                "order_issues": [issue.model_dump(mode="json") for issue in issues[:12]],
                "pending_changes": [change.model_dump(mode="json") for change in pending],
            },
            "recent_orders": await backend.recent_orders(),
            "recent_changes": [change.model_dump(mode="json") for change in resolved[:12]],
            "trends": await backend.kpi_trends(session),
            "limitations": (await backend.get_merchant_context(session) or {}).get(
                "limitations", []
            ),
        }

    @router.get("/alerts")
    async def alerts(record: CurrentSession) -> dict:
        session = context(record)
        inventory = await backend.get_inventory_alerts(session)
        issues = await backend.get_order_issues(session)
        return {
            "inventory": [alert.model_dump(mode="json") for alert in inventory],
            "order_issues": [issue.model_dump(mode="json") for issue in issues],
        }

    @router.get("/listings")
    async def listings(record: CurrentSession, query: str = "", limit: int = 12) -> dict:
        found = await backend.search_listings(context(record), query, None, limit)
        return {
            "total": len(found),
            "listings": [listing.model_dump(mode="json") for listing in found],
        }

    @router.get("/listings/{listing_id:path}")
    async def listing(listing_id: str, record: CurrentSession) -> dict:
        session = context(record)
        details = await backend.get_listing(session, listing_id)
        if details is None:
            raise HTTPException(status_code=404, detail="Listing not found")
        pricing = await backend.get_pricing_context(session, listing_id)
        return {
            "listing": details.model_dump(mode="json"),
            "pricing": pricing.model_dump(mode="json") if pricing else None,
        }

    async def change_action(change_id: str, action: str, record: Any) -> dict:
        """The preview card's Approve and Dismiss buttons, through the same executor as the
        assistant's own tools. The approval mark is set for this call only."""
        if action == "apply_change":
            record.state.approved_change_ids.add(change_id)
        else:
            record.state.host_action_change_ids.add(change_id)
        executor = executor_class(
            backend=backend,
            config=agent.config,
            skills=agent.skills,
            session=context(record),
            state=record.state,
            memory=agent.memory,
        )
        try:
            execution = await executor.execute(action, {"change_id": change_id})
        finally:
            # Neither mark outlives the click, whatever happened.
            record.state.host_action_change_ids.discard(change_id)
            record.state.approved_change_ids.discard(change_id)
        if execution.is_error:
            raise HTTPException(status_code=400, detail=execution.result_text)
        if execution.blocked is not None:
            return {"ok": False, "change": None, "reason": execution.result_text}
        verb = "approved and applied" if action == "apply_change" else "dismissed"
        record.pending_app_events.append(
            f"Operator {verb} change {change_id} from the preview card."
        )
        change = next(
            (
                event.data.get("change")
                for event in execution.events
                if event.type == "change_update"
            ),
            None,
        )
        return {"ok": True, "change": change}

    @router.post("/changes/{change_id:path}/apply")
    async def apply_change(change_id: str, record: CurrentSession) -> dict:
        return await change_action(change_id, "apply_change", record)

    @router.post("/changes/{change_id:path}/discard")
    async def discard_change(change_id: str, record: CurrentSession) -> dict:
        return await change_action(change_id, "discard_change", record)

    install_memory_routes(
        router, "/memory", current_session=CurrentSession, memory_store=agent.memory.store
    )

    @router.post("/reset")
    async def reset(record: CurrentSession) -> dict:
        sessions.reset(record)
        return {"ok": True}

    @router.get("/health")
    async def health() -> dict:
        return {"ok": True, "merchant": merchant_id}

    return router


def merchant_state_type() -> type[MerchantSessionState]:
    return MerchantSessionState
