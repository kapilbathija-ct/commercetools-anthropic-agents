"""One turn end to end, with a stub backend and a fake model client.

This is the proof the wiring holds: prompt, tools, skills, gates, executor and the session
store all take part, and no network call is made.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from commerce_common.memory import InMemoryMemoryStore
from commerce_common.testing import FakeClient, text_message
from shopping_agent import (
    Cart,
    FulfillmentOption,
    Order,
    Policy,
    ProductDetails,
    ShoppingSessionState,
    StorefrontBackend,
    UserPreferences,
)
from shopping_agent_runtime import ShoppingAgent

from ct_shopping import CTShoppingSession, CTShoppingToolExecutor, build_shopping_config

ROOT = Path(__file__).resolve().parents[1]


class StubStorefront(StorefrontBackend):
    """Every method answers, so a turn can run without commercetools."""

    async def search_products(self, session, query, filters=None, limit=8):
        return []

    async def get_product_details(self, session, product_id) -> ProductDetails | None:
        return None

    async def get_cart(self, session) -> Cart:
        return Cart()

    async def add_to_cart(self, session, product_id, quantity) -> Cart:
        return Cart()

    async def update_cart_item(self, session, product_id, quantity) -> Cart:
        return Cart()

    async def remove_from_cart(self, session, product_id) -> Cart:
        return Cart()

    async def get_preferences(self, session) -> UserPreferences:
        return UserPreferences(user_id=session.user_id)

    async def get_orders(self, session, limit=5) -> list[Order]:
        return []

    async def get_order(self, session, order_id) -> Order | None:
        return None

    async def search_policies(self, session, query) -> list[Policy]:
        return []

    async def get_fulfillment_options(self, session, product_ids) -> list[FulfillmentOption]:
        return []


def build_agent(*responses: object) -> tuple[ShoppingAgent, FakeClient]:
    fake = FakeClient(list(responses) or [text_message("Hello, how can I help?")])
    agent = ShoppingAgent(
        backend=StubStorefront(),
        skills_dir=ROOT / "ct_shopping" / "skills",
        config=build_shopping_config(),
        client=fake,
        memory_store=InMemoryMemoryStore(),
        executor_class=CTShoppingToolExecutor,
    )
    return agent, fake


@pytest.mark.asyncio
async def test_a_turn_streams_text_back():
    agent, _fake = build_agent()
    session = CTShoppingSession(session_id="s-1", user_id="anon-1", is_guest=True)
    state = ShoppingSessionState()
    messages = [{"role": "user", "content": "hello"}]
    events = [event async for event in agent.stream_turn(messages, session, state)]
    types = [event.type for event in events]
    assert "text_delta" in types
    assert types[-1] == "turn_complete"
    text = "".join(e.data.get("text", "") for e in events if e.type == "text_delta")
    assert "Hello" in text


@pytest.mark.asyncio
async def test_the_five_flows_are_indexed_and_the_prompt_is_stable():
    """The static prompt is the same bytes on every turn, which is what makes the cache
    read on a second turn possible."""
    agent, fake = build_agent(text_message("one"), text_message("two"))
    session = CTShoppingSession(session_id="s-2", user_id="anon-2", is_guest=True)
    state = ShoppingSessionState()
    messages: list[dict] = [{"role": "user", "content": "a"}]
    assert [e async for e in agent.stream_turn(messages, session, state)]
    messages.append({"role": "user", "content": "b"})
    assert [e async for e in agent.stream_turn(messages, session, state)]

    cached = {
        block["text"]
        for call in fake.calls
        for block in call["system"]
        if block.get("cache_control")
    }
    assert len(cached) == 1, "the cached prompt prefix changed between turns"
    # Every flow this deployment indexes is offered to the model.
    prompt = next(iter(cached))
    for flow in (
        "search-discovery",
        "purchase-research",
        "planning-goals",
        "customer-care",
        "memory-personalization",
    ):
        assert flow in prompt, f"{flow} is not in the skill index"
