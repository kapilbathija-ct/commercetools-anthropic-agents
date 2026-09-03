"""One live conversation through the service: real commercetools, real model.

    python scripts/smoke_chat.py

Prints each turn's text and the tool calls it made, plus the cache read on the second turn
-- a non-zero ``cache_read_input_tokens`` is the proof the static prompt held its bytes.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic  # noqa: E402
from commerce_common.memory import InMemoryMemoryStore  # noqa: E402
from shopping_agent import ShoppingSessionState  # noqa: E402
from shopping_agent_runtime import ShoppingAgent  # noqa: E402

from ct_common import CTClient, load_settings  # noqa: E402
from ct_shopping import (  # noqa: E402
    CommercetoolsStorefront,
    CTShoppingSession,
    CTShoppingToolExecutor,
    build_shopping_config,
)

ROOT = Path(__file__).resolve().parents[1]

TURNS = [
    "I'm after a chair for a small home office, ideally under $150. What do you have?",
    "What's your return policy if it doesn't fit the space?",
    "Add the cheapest one to my cart.",
    "What's in my cart now, and what would shipping cost?",
]


async def main() -> None:
    settings = load_settings(ROOT / ".env")
    ct = CTClient(settings)
    backend = CommercetoolsStorefront(ct, checkout_url="http://localhost:3000/checkout")
    agent = ShoppingAgent(
        backend=backend,
        skills_dir=ROOT / "ct_shopping" / "skills",
        config=build_shopping_config(),
        client=anthropic.AsyncAnthropic(),
        memory_store=InMemoryMemoryStore(),
        executor_class=CTShoppingToolExecutor,
    )
    session = CTShoppingSession(
        session_id="smoke-" + Path(__file__).stem, user_id="anon-smoke-test", is_guest=True
    )
    state = ShoppingSessionState()
    messages: list[dict] = []
    try:
        for turn, prompt in enumerate(TURNS, start=1):
            messages.append({"role": "user", "content": prompt})
            print(f"\n=== turn {turn}: {prompt}")
            text: list[str] = []
            tools: list[str] = []
            ui: list[str] = []
            complete: dict = {}
            async for event in agent.stream_turn(messages, session, state):
                if event.type == "text_delta":
                    text.append(event.data.get("text", ""))
                elif event.type == "tool_call":
                    tools.append(event.data.get("tool", "?"))
                elif event.type == "ui":
                    ui.append(event.data.get("component", "?"))
                elif event.type == "turn_complete":
                    complete = event.data
            print(f"  tools: {tools or ['none']}")
            print(f"  ui:    {ui or ['none']}")
            print(f"  reply: {''.join(text).strip()[:600]}")
            usage = complete.get("usage") or {}
            print(
                f"  cache_read={usage.get('cache_read_input_tokens', 0)} "
                f"cache_write={usage.get('cache_creation_input_tokens', 0)} "
                f"in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)} "
                f"({complete.get('elapsed_ms', 0)} ms)"
            )
            print(f"  products in provenance: {len(state.seen_products)}")
    finally:
        await ct.aclose()


if __name__ == "__main__":
    asyncio.run(main())
