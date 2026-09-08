# Debugger walkthrough — stepping through a real turn from the browser

Better than running `scripts/trace_turn.py` on camera, for one reason: the audience watches a
**real request from the real browser** and sees the actual values — the `tool_use` block
Anthropic returned, the raw commercetools JSON, the mapped records — rather than a summary
someone wrote.

Keep the tracer for two jobs it does better: the **egress proof** (every host contacted,
which a debugger cannot show in one frame) and as a **fallback** if the debugger misbehaves
live. Details at the end.

## Setup

`.vscode/launch.json` has **Agent API (debug, for a walkthrough)**. Two settings in it are
load-bearing:

- **`"justMyCode": false`** — without it, breakpoints inside `.venv` never bind, and the two
  most interesting frames (the model call and the tool dispatch) are in the reference
  packages, not our code.
- **`"autoReload": false`** — a reload mid-demo restarts the process and drops the session.

Stop any `uvicorn` you already have on :8000 first, then start this config. Leave the shop
(:3005) and portal (:3105) running normally — they are not being debugged.

## The seven breakpoints, in the order they are hit

Set them all before you start. Numbers are current as of this commit; if the reference
packages are re-pinned, re-find them with the greps at the bottom.

| # | File | Line | What to show in Variables |
|---|---|---|---|
| 1 | `service/main.py` | `228` (`async def chat`) | `request.message` — the user's words arriving |
| 2 | `.venv/…/shopping_agent_runtime/orchestrator.py` | `209` | `request` — the whole model payload: `system`, `tools` (21), `messages` |
| 3 | `.venv/…/commerce_common/execution.py` | `240` | `name`, `tool_input`, then `handler` |
| 4 | `ct_shopping/backend.py` | `145` (`search_products`) | `query`, `filters.max_price` |
| 5 | `ct_common/client.py` | `125` (`graphql`) | `query`, `variables` |
| 6 | `ct_common/mapping.py` | `392` (`to_product`) | `projection` in, `Product` out |
| 7 | `.venv/…/shopping_agent_runtime/orchestrator.py` | `261` | the `tool_result` block going back |

For the merchant path swap 4 for `ct_merchant/backend.py:530` (`get_inventory_alerts`) and 2
and 7 for `merchant_agent_runtime/orchestrator.py` (`239` and the same append).

## The narration, breakpoint by breakpoint

Type **"show me some chairs under $1000"** in the shop, then hit F5 through each stop.

**1 — `service/main.py:228`.** "This is our own FastAPI route. The message arrives, gets
appended to the session transcript, and the turn opens as a streamed response."

**2 — `orchestrator.py:209`.** Expand `request` in Variables. **This is the frame that
answers the MCP question.** Show three things:
- `system` — the static prompt, the same bytes every turn
- `tools` — 21 definitions, **ours**, built from our config
- and what is *absent*: no `mcp_servers` key, no `mcp_toolset` entry

> "If this were the MCP route, there'd be an `mcp_servers` block here pointing at a
> commercetools MCP server, and Anthropic would call it for us. There isn't one. We hand it
> tool *definitions* and it hands back a decision."

**3 — `execution.py:240`.** The money shot. `name` is `'search_products'`, `tool_input` is
`{'query': 'chair', 'filters': {'max_price': 1000}}`, and `handler` is a **bound method of
our own class**.

> "That's the entire scaffolding between the model saying a name and our Python running. One
> dictionary lookup. No network hop, no MCP server, no dynamic dispatch."

Then **open the Call Stack panel** and read it downwards — route, orchestrator, executor,
about to enter our backend. Nothing between them.

**4 — `ct_shopping/backend.py:145`.** "Now we're in code I wrote. `$1000` arrived as a typed
filter, not a string, because the tool schema said so."

**5 — `ct_common/client.py:125`.** Show `variables` — the price selection and locale are
there. "Without those, commercetools returns results with no name and no price at all."
Step over and inspect the response: raw projections, tens of kilobytes.

**6 — `ct_common/mapping.py:392`.** The answer to "how is the response interpreted". Show
`projection` going in and the `Product` coming out, and name the three decisions: options
from attributes that *differ* across variants, `price.discounted.value` not the list price,
name from `nameAllLocales`.

**7 — `orchestrator.py:261`.** "The result goes back as another message in the same
conversation, and the loop returns to breakpoint 2." Continue, and **breakpoint 2 hits
again** — that is the round trip made visible. Let it run out to the cards rendering.

## Things that will bite you live

- **The browser request is a long-lived SSE stream, and you are pausing it.** With seven
  breakpoints the fetch can exceed the browser's patience and the UI may show an error even
  though the server finished. Two options: disable breakpoints 5–7 for the first pass and
  re-enable them for a second, or accept the UI error and say so — the point is the code
  path, not the render.
- **Breakpoint 2 is hit three times** in one turn. That is the demo, not a problem — but say
  it before it happens or it looks like a mistake.
- **Don't step *into* the streaming context manager** at breakpoint 2. Step over it. Stepping
  into the SDK's stream internals is a long detour into machinery nobody asked about.
- **Async frames.** Prefer **F5 (continue)** between breakpoints over F11 (step into). The
  interesting jumps are all between breakpoints anyway.
- **`.venv` breakpoints silently do nothing** if `justMyCode` reverts to `true`. If a
  breakpoint shows hollow rather than solid red, that is why.
- Start from a **fresh browser session** if the cart already has items — a grounding read of
  a full cart adds noise to breakpoint 2's `messages`.

## Keep the tracer for these two

**The egress proof.** A debugger cannot show "every host this turn contacted" in one frame.
Run this once, at the end, as the closing evidence:

```bash
python scripts/trace_turn.py shop "show me some chairs under \$1000"
```

…and read the last four lines: three hosts, `MCP endpoints contacted: NONE`.

**And as insurance.** If the debugger misbehaves on the day — a stale breakpoint, a browser
timeout you can't talk past — the tracer produces the same story non-interactively in ten
seconds.

## Re-finding the line numbers after a re-pin

```bash
V=.venv/lib/python3.13/site-packages
grep -n "async def chat" service/main.py
grep -n "client.messages.stream" $V/shopping_agent_runtime/orchestrator.py
grep -n "handler = self._handlers.get(name)" $V/commerce_common/execution.py
grep -n "async def search_products" ct_shopping/backend.py
grep -n "async def graphql" ct_common/client.py
grep -n "^def to_product" ct_common/mapping.py
grep -n "tool_result_block(block.id, outcome)" $V/shopping_agent_runtime/orchestrator.py
```
