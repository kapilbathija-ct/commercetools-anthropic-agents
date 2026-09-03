"""The shopping agent as this project's own service.

    uvicorn service.main:app --reload --port 8000

Routes carry a session id in ``X-Session-Id`` and nothing else that identifies anyone.
Session start is the one place a principal enters: it takes the customer id the caller's
own authentication resolved, or none for a guest, and everything afterwards reads the
principal off the record. That is the line real authentication replaces -- see the TODO on
``start_session``.

Run one worker until ``REDIS_URL`` is set. With the in-process store a second worker
splits sessions and the shopper's transcript silently follows whichever process answered.
"""

# Route parameters are annotated with dependencies built at call time, so this module
# evaluates its annotations eagerly (no ``from __future__ import annotations``).

import logging
import os
from pathlib import Path
from typing import Any

import anthropic
from commerce_common.memory import InMemoryMemoryStore, MemoryWriteRejected
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from merchant_agent import MerchantSessionState
from merchant_agent_runtime import MerchantAgent
from pydantic import BaseModel, Field
from shopping_agent import PageContext, ShoppingSessionState, StorefrontBackend
from shopping_agent.fencing import STOREFRONT_FENCE
from shopping_agent.serialization import cart_payload
from shopping_agent_runtime import ShoppingAgent

from ct_common import CTClient, ReferenceCache, load_settings
from ct_common.checkout import CheckoutSessions
from ct_common.sync_client import CTSyncClient
from ct_merchant import (
    CommercetoolsMerchant,
    CTMerchantToolExecutor,
    build_merchant_config,
)
from ct_merchant.ledger import CustomObjectChangeLedger
from ct_shopping import (
    CommercetoolsStorefront,
    CTShoppingSession,
    CTShoppingToolExecutor,
    build_shopping_config,
)

from .checkout import install_checkout_routes
from .ct_sessions import CustomObjectSessionStore
from .host import append_user_turn, build_app, load_demo_env, stream_turn
from .memory import MemoryFactEdit, install_memory_routes
from .merchant import build_merchant_router
from .redis_sessions import build_session_store
from .sessions import session_dependency

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]

load_demo_env(ROOT)
settings = load_settings(ROOT / ".env")


def build_agent(backend: StorefrontBackend, model_client: Any = None) -> ShoppingAgent:
    return ShoppingAgent(
        backend=backend,
        skills_dir=ROOT / "ct_shopping" / "skills",
        config=build_shopping_config(),
        client=model_client or anthropic.AsyncAnthropic(),
        memory_store=InMemoryMemoryStore(),
        executor_class=CTShoppingToolExecutor,
    )


def create_app(
    *,
    backend: StorefrontBackend | None = None,
    model_client: Any = None,
    redis_url: str | None = ...,  # type: ignore[assignment]
) -> FastAPI:
    """The service. A test passes its own backend and model client; the module-level app
    below is what uvicorn serves."""
    ct_client = None
    if backend is None:
        ct_client = CTClient(settings)
        backend = CommercetoolsStorefront(ct_client, cache=ReferenceCache())
    agent = build_agent(backend, model_client)
    store_url = settings.redis_url if redis_url is ... else redis_url
    # Three stores, in order of preference for a deployment with no instance affinity:
    # Redis when a URL is configured, commercetools Custom Objects when asked for (no extra
    # vendor, and the platform's own compare-and-set), and the in-process store otherwise --
    # which is correct for exactly one long-lived worker and nothing else.
    sync_client = None
    if store_url:
        sessions = build_session_store(ShoppingSessionState, store_url)
    elif os.environ.get("SESSION_STORE") == "commercetools":
        sync_client = CTSyncClient(settings)
        sessions = CustomObjectSessionStore(ShoppingSessionState, sync_client)
    else:
        sessions = build_session_store(ShoppingSessionState, None)
    current_session = session_dependency(sessions, "/api/session")
    app = build_app(title="commercetools shopping agent")
    register_routes(app, agent=agent, backend=backend, sessions=sessions, current=current_session)
    checkout = CheckoutSessions(
        settings,
        application_key=os.environ.get("CTP_CHECKOUT_APP_KEY", ""),
        processor_url=os.environ.get("CTP_CHECKOUT_PROCESSOR_URL") or None,
    )
    install_checkout_routes(
        app,
        backend=backend,
        sessions=checkout,
        current=current_session,
        context=context,
    )

    # The merchant half, in the same process so an approved change shows in the shop at
    # once. Its own session store and state type: an operator session is not a shopper's.
    if ct_client is not None:
        merchant_config = build_merchant_config()
        # Staging and approving are two different requests, so the ledger has to outlive
        # the process that staged the change.
        merchant_ledger = (
            CustomObjectChangeLedger(merchant_config, sync_client) if sync_client else None
        )
        merchant_backend = CommercetoolsMerchant(
            ct_client, config=merchant_config, ledger=merchant_ledger
        )
        merchant_agent = MerchantAgent(
            backend=merchant_backend,
            skills_dir=ROOT / "ct_merchant" / "skills",
            config=merchant_config,
            client=model_client or anthropic.AsyncAnthropic(),
            memory_store=InMemoryMemoryStore(),
            executor_class=CTMerchantToolExecutor,
        )
        if store_url:
            merchant_sessions = build_session_store(MerchantSessionState, store_url)
        elif sync_client is not None:
            merchant_sessions = CustomObjectSessionStore(MerchantSessionState, sync_client)
        else:
            merchant_sessions = build_session_store(MerchantSessionState, None)
        app.include_router(
            build_merchant_router(
                backend=merchant_backend,
                agent=merchant_agent,
                sessions=merchant_sessions,
                executor_class=CTMerchantToolExecutor,
                merchant_id=settings.project_key,
                operator=os.environ.get("MERCHANT_OPERATOR", "operator@acme.test"),
                env_hint=str(ROOT / ".env"),
            )
        )
        app.state.merchant_sessions = merchant_sessions
        app.state.merchant_backend = merchant_backend
    app.state.ct_client = ct_client
    app.state.sessions = sessions
    return app


class StartSessionRequest(BaseModel):
    # The customer id the caller's own authentication resolved. Absent means a guest, and
    # the session is then known to commercetools by an anonymous id of its own.
    customer_id: str | None = Field(default=None, max_length=128)


class CartAddRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=256)
    quantity: int = Field(default=1, ge=1, le=99)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    page: PageContext | None = None


GUEST_PREFIX = "anon-"


def is_guest_principal(user_id: str) -> bool:
    """A guest's principal is the anonymous id the session was started with. One
    convention, in one place, because the whole cart identity model turns on it: a Cart's
    ``customerId`` is only valid for a registered Customer, so a guest's identity has to go
    in ``anonymousId`` instead -- and a cart created with the wrong one silently ends up
    with neither field set."""
    return user_id.startswith(GUEST_PREFIX)


def catalog_session() -> CTShoppingSession:
    """A public catalogue read still needs a session object for the backend contract, but
    it identifies nobody: the catalogue does not vary by shopper here, and a cart write is
    impossible without a real session id."""
    return CTShoppingSession(session_id="catalog", user_id=f"{GUEST_PREFIX}catalog", is_guest=True)


def context(record: Any, page: PageContext | None = None) -> CTShoppingSession:
    """The per-request session context. Identity comes off the record, never off the
    request: no route and no tool argument carries a customer id."""
    return CTShoppingSession(
        session_id=record.session_id,
        user_id=record.user_id,
        is_guest=is_guest_principal(record.user_id),
        page=page or PageContext(),
    )


def register_routes(app: FastAPI, *, agent: Any, backend: Any, sessions: Any, current: Any) -> None:
    CurrentSession = current

    @app.post("/api/session")
    async def start_session(request: StartSessionRequest | None = None) -> dict:
        """TODO: authenticate the caller here and pass the principal that verification
        resolved. Until then the customer id is taken on trust from the request body, which
        is the one place in this service where that is true."""
        body = request or StartSessionRequest()
        user_id = body.customer_id or f"{GUEST_PREFIX}{os.urandom(8).hex()}"
        record = sessions.start(user_id)
        profile = await backend.get_preferences(context(record))
        return {
            "session_id": record.session_id,
            "guest": is_guest_principal(user_id),
            "name": profile.display_name,
        }

    @app.post("/api/chat")
    async def chat(request: ChatRequest, record: CurrentSession) -> StreamingResponse:
        append_user_turn(record, request.message, "App events")
        return stream_turn(
            agent, sessions, record, context(record, request.page), env_hint=str(ROOT / ".env")
        )

    @app.get("/api/cart")
    async def cart(record: CurrentSession) -> dict:
        return cart_payload(await backend.get_cart(context(record)))

    @app.post("/api/reset")
    async def reset(record: CurrentSession) -> dict:
        sessions.reset(record)
        return {"ok": True}

    # The catalogue is public, as a storefront's own product pages are: these routes carry
    # no session and write no provenance. Provenance is what the *agent* read this session,
    # and the only direct add-to-cart path in the app runs off the agent's own product
    # cards, so nothing here needs to enter it. A route with no authenticated session must
    # not be able to widen what the model is later allowed to write.
    @app.get("/api/products")
    async def list_products(limit: int = 60) -> dict:
        products = await backend.browse_products(catalog_session(), limit=limit)
        return {"products": [product.model_dump(mode="json") for product in products]}

    @app.get("/api/products/{product_id:path}")
    async def get_product(product_id: str) -> dict:
        product = await backend.get_product_details(catalog_session(), product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return product.model_dump(mode="json")

    @app.post("/api/cart/add")
    async def cart_add(request: CartAddRequest, record: CurrentSession) -> dict:
        """The tile's Add button, run through the same executor as the agent's
        ``add_to_cart`` so the provenance gate and the quantity caps hold for a click
        exactly as they do for a tool call."""
        executor = agent.executor_class(
            backend=backend,
            config=agent.config,
            skills=agent.skills,
            session=context(record),
            state=record.state,
            memory=agent.memory,
        )
        execution = await executor.execute(
            "add_to_cart", {"product_id": request.product_id, "quantity": request.quantity}
        )
        if execution.blocked or execution.is_error:
            raise HTTPException(status_code=400, detail=execution.result_text.split(". ")[0] + ".")
        product = record.state.seen_products.get(request.product_id)
        if product is None:
            raise HTTPException(status_code=400, detail="Product not in this session's results")
        # The next turn is told what happened outside the conversation. The title is
        # catalogue-authored text entering model context unfenced, so it is sanitized.
        record.pending_app_events.append(
            f"Customer tapped Add to cart on "
            f"{STOREFRONT_FENCE.sanitize_text(product.title, max_chars=120)} "
            f"({product.product_id}), quantity {request.quantity}."
        )
        cart = next(
            (event.data.get("cart") for event in execution.events if event.type == "cart_update"),
            None,
        )
        return {"ok": True, "cart": cart}

    @app.get("/api/orders")
    async def list_orders(record: CurrentSession) -> dict:
        session = context(record)
        if session.is_guest:
            # A guest has no order history; the storefront shows the empty state rather
            # than an error.
            return {"orders": [], "guest": True}
        orders = await backend.get_orders(session, limit=20)
        return {"orders": [order.model_dump(mode="json") for order in orders]}

    install_memory_routes(
        app, "/api/memory", current_session=CurrentSession, memory_store=agent.memory.store
    )

    @app.patch("/api/memory")
    async def edit_memory_fact(edit: MemoryFactEdit, record: CurrentSession) -> dict:
        store = agent.memory.store
        existing = {fact.key: fact for fact in await store.get_facts(record.user_id)}
        if edit.key not in existing:
            raise HTTPException(status_code=404, detail="No such fact")
        try:
            corrected = agent.memory.validate(
                edit.key,
                edit.value,
                existing[edit.key].category.value,
                source_session_id=existing[edit.key].source_session_id,
            )
        except MemoryWriteRejected as rejected:
            raise HTTPException(status_code=400, detail=str(rejected)) from None
        if not corrected.value:
            raise HTTPException(status_code=400, detail="Value must not be empty")
        await store.upsert_facts(record.user_id, [corrected])
        return {"ok": True, "fact": corrected.model_dump(mode="json")}

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "project": settings.project_key, "store": settings.store_key}


app = create_app()
