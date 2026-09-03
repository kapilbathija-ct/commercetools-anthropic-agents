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
from commerce_common.memory import InMemoryMemoryStore
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from shopping_agent import PageContext, ShoppingSessionState, StorefrontBackend
from shopping_agent.serialization import cart_payload
from shopping_agent_runtime import ShoppingAgent

from ct_common import CTClient, ReferenceCache, load_settings
from ct_shopping import (
    CommercetoolsStorefront,
    CTShoppingSession,
    CTShoppingToolExecutor,
    build_shopping_config,
)

from .host import append_user_turn, build_app, load_demo_env, stream_turn
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
    sessions = build_session_store(ShoppingSessionState, store_url)
    current_session = session_dependency(sessions, "/api/session")
    app = build_app(title="commercetools shopping agent")
    register_routes(app, agent=agent, backend=backend, sessions=sessions, current=current_session)
    app.state.ct_client = ct_client
    app.state.sessions = sessions
    return app


class StartSessionRequest(BaseModel):
    # The customer id the caller's own authentication resolved. Absent means a guest, and
    # the session is then known to commercetools by an anonymous id of its own.
    customer_id: str | None = Field(default=None, max_length=128)


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

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "project": settings.project_key, "store": settings.store_key}


app = create_app()
