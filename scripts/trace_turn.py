"""Trace one agent turn end to end, for a code-walkthrough recording.

Runs a real turn against the live project and prints a single time-ordered timeline that
interleaves three things:

* the agent's own decisions (model round, tool call, tool result, ui event)
* **every outbound HTTP request**, with host, method, path, status and duration
* the code that made each hop, as ``file.py:function``

The point of the egress log is what it does *not* contain. Every host contacted is printed
in the summary, so an implementer can see there is no MCP endpoint in the path -- the agent
reaches commercetools through this process's own client, and the only other host is the
Messages API.

    python scripts/trace_turn.py shop     "show me some chairs under $1000"
    python scripts/trace_turn.py merchant "give me a list of slow selling products from the past week"
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import anthropic
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commerce_common.memory import InMemoryMemoryStore  # noqa: E402
from merchant_agent import MerchantSessionContext, MerchantSessionState  # noqa: E402
from merchant_agent.tools.registry import build_tools as build_merchant_tools  # noqa: E402
from merchant_agent_runtime import MerchantAgent  # noqa: E402
from shopping_agent import ShoppingSessionState  # noqa: E402
from shopping_agent.tools.registry import build_tools as build_shopping_tools  # noqa: E402
from shopping_agent_runtime import ShoppingAgent  # noqa: E402

from ct_common import CTClient, load_settings  # noqa: E402
from ct_common.sync_client import CTSyncClient  # noqa: E402
from ct_merchant import (  # noqa: E402
    CommercetoolsMerchant,
    CTMerchantToolExecutor,
    build_merchant_config,
)
from ct_merchant.ledger import CustomObjectChangeLedger  # noqa: E402
from ct_shopping import (  # noqa: E402
    CommercetoolsStorefront,
    CTShoppingSession,
    CTShoppingToolExecutor,
    build_shopping_config,
)

ROOT = Path(__file__).resolve().parents[1]
START = time.time()
HOSTS: dict[str, int] = {}


def stamp() -> str:
    return f"[{time.time() - START:6.2f}s]"


def line(kind: str, text: str, where: str = "") -> None:
    tail = f"    ({where})" if where else ""
    print(f"{stamp()} {kind:<9} {text}{tail}")


def label_for(request: httpx.Request) -> tuple[str, str]:
    """Which system a request is going to, and how to describe it usefully.

    A GraphQL hop is named by its operation, because "POST /graphql" three times in a row
    tells a viewer nothing about what the agent actually asked for.
    """
    url = request.url
    host = url.host
    if "anthropic" in host:
        return "MODEL", str(url.path)
    if host.startswith("auth."):
        return "CT-AUTH", str(url.path)
    if "commercetools" in host:
        if url.path.endswith("/graphql"):
            try:
                body = json.loads(request.content)
                match = re.search(r"(?:query|mutation)\s+(\w+)", body.get("query", ""))
                op = match.group(1) if match else "?"
            except Exception:
                op = "?"
            return "CT", f"GraphQL {op}"
        path = "/" + url.path.split("/", 2)[-1]
        return "CT", f"REST {path}{'?' + url.query.decode() if url.query else ''}"[:88]
    return "OTHER", str(url)


def hooks() -> dict[str, list[Any]]:
    pending: dict[int, float] = {}

    async def on_request(request: httpx.Request) -> None:
        pending[id(request)] = time.time()

    async def on_response(response: httpx.Response) -> None:
        started = pending.pop(id(response.request), time.time())
        kind, what = label_for(response.request)
        HOSTS[response.request.url.host] = HOSTS.get(response.request.url.host, 0) + 1
        line(
            f"-> {kind}",
            f"{what}  {response.status_code}  {(time.time() - started) * 1000:.0f}ms",
        )

    return {"request": [on_request], "response": [on_response]}


async def run(which: str, message: str) -> None:
    settings = load_settings(ROOT / ".env")
    traced = httpx.AsyncClient(timeout=httpx.Timeout(60.0), event_hooks=hooks())

    print(f"\n{'=' * 100}\nTURN: {message!r}   ({which} agent)\n{'=' * 100}")
    line(
        "ENTRY",
        f"POST /api/{'merchant/' if which == 'merchant' else ''}chat",
        "service/main.py:chat -> service/host.py:stream_turn",
    )

    ct = CTClient(settings, client=traced)
    model = anthropic.AsyncAnthropic(http_client=traced)

    if which == "shop":
        backend = CommercetoolsStorefront(ct)
        agent = ShoppingAgent(
            backend=backend,
            skills_dir=ROOT / "ct_shopping" / "skills",
            config=build_shopping_config(),
            client=model,
            memory_store=InMemoryMemoryStore(),
            executor_class=CTShoppingToolExecutor,
        )
        session: Any = CTShoppingSession(session_id="trace", user_id="anon-trace", is_guest=True)
        state: Any = ShoppingSessionState()
        backend_file = "ct_shopping/backend.py"
    else:
        config = build_merchant_config()
        backend = CommercetoolsMerchant(
            ct,
            config=config,
            ledger=CustomObjectChangeLedger(config, CTSyncClient(settings)),
        )
        agent = MerchantAgent(
            backend=backend,
            skills_dir=ROOT / "ct_merchant" / "skills",
            config=config,
            client=model,
            memory_store=InMemoryMemoryStore(),
            executor_class=CTMerchantToolExecutor,
        )
        session = MerchantSessionContext(
            session_id="trace", merchant_id=settings.project_key, operator="Kapil"
        )
        state = MerchantSessionState()
        backend_file = "ct_merchant/backend.py"

    # The static prompt and the tool list are built once per process and are the same bytes
    # on every turn, which is what makes the cache read at the end of this trace possible.
    builder = build_shopping_tools if which == "shop" else build_merchant_tools
    tool_count = len(builder(agent.config, agent.skills.names))
    role = "shopping_agent" if which == "shop" else "merchant_agent"
    line(
        "PROMPT",
        f"static prompt + {tool_count} tool definitions, identical every turn",
        f"{role}/prompt.py + {role}/tools/registry.py:build_tools",
    )

    messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
    text: list[str] = []
    try:
        async for event in agent.stream_turn(messages, session, state):
            if event.type == "tool_call":
                tool = str(event.data.get("tool"))
                line(
                    "TOOL-CALL",
                    f"{tool}  {str(event.data.get('input'))[:150]}",
                    "commerce_common/execution.py:execute -> handlers[name]",
                )
                if tool == "load_skill":
                    # Served from the in-process skill registry, not the backend.
                    line(
                        "",
                        "          -> skills registry, no platform call",
                        "commerce_common/skills.py",
                    )
                elif tool.startswith("present_") or tool == "checkout":
                    # A presentation call is validated and enriched in this
                    # process from what the session already saw. It makes no
                    # platform call at all -- saying otherwise in a teaching
                    # trace would be worse than saying nothing.
                    line(
                        "",
                        "          -> enriched from session provenance, no platform call",
                        f"{role}/enrichment.py",
                    )
                else:
                    line("", f"          -> backend.{tool}()", backend_file)
            elif event.type == "tool_result":
                preview = str(event.data.get("text", ""))[:70].replace("\n", " ")
                line(
                    "TOOL-RSLT",
                    f"fenced back to the model: {preview}...",
                    "commerce_common/fencing.py",
                )
            elif event.type == "ui":
                line(
                    "UI-EVENT",
                    f"component={event.data.get('component')} -> SSE to the browser",
                    "commerce_common/streaming.py:to_sse",
                )
            elif event.type == "text_delta":
                text.append(event.data.get("text", ""))
            elif event.type == "turn_complete":
                u = event.data.get("usage") or {}
                line(
                    "DONE",
                    f"stop={event.data.get('stop_reason')}  "
                    f"cache_read={u.get('cache_read_input_tokens', 0)}  "
                    f"in={u.get('input_tokens', 0)} out={u.get('output_tokens', 0)}  "
                    f"{event.data.get('elapsed_ms', 0)}ms",
                )
    finally:
        await traced.aclose()

    print(f"\nREPLY: {''.join(text).strip()[:400]}")
    print(f"\n{'-' * 100}\nEVERY HOST THIS TURN CONTACTED")
    for host, n in sorted(HOSTS.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3} x  {host}")
    mcp = [h for h in HOSTS if "mcp" in h.lower()]
    print(f"\n  MCP endpoints contacted: {mcp or 'NONE'}")
    print("  -> the agent reached commercetools through this process's own HTTP client.")
    print("-" * 100)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "shop"
    msg = sys.argv[2] if len(sys.argv) > 2 else "show me some chairs under $1000"
    asyncio.run(run(which, msg))
