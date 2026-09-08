# Code-path walkthrough — one turn, end to end, no Commerce MCP

For a recording aimed at implementers. Two traces: a shopping turn and a merchant turn.

```bash
python scripts/trace_turn.py shop     "show me some chairs under \$1000"
python scripts/trace_turn.py merchant "give me a list of slow selling products from the past week"
```

The tracer runs a **real** turn against the live project and prints one time-ordered
timeline: the agent's decisions, every outbound HTTP request with host and duration, and the
file that made each hop. It ends by listing every host contacted — which is the proof.

---

## The nine steps, with the file that owns each

| # | Step | Where |
|---|---|---|
| 1 | HTTP entry, message appended to the transcript | `service/main.py:chat` → `service/host.py:append_user_turn` |
| 2 | Turn opens, streamed as SSE | `service/host.py:stream_turn` → `ShoppingAgent.stream_turn` |
| 3 | Static prompt + 21 tool definitions, identical every turn | `shopping_agent/prompt.py`, `tools/registry.py:build_tools` |
| 4 | **Model call #1** — the only place a model is contacted | `shopping_agent_runtime/orchestrator.py:209` (`client.messages.stream`) |
| 5 | Model returns `stop_reason=tool_use` with a `tool_use` block | orchestrator `:233` collects the blocks |
| 6 | Tool name → Python function | `commerce_common/execution.py:214 execute` → `:240 self._handlers[name]` |
| 7 | Gates, then **our** backend method | `shopping_agent/gates.py`, then `ct_shopping/backend.py` |
| 8 | commercetools call, response mapped to typed records, fenced | `ct_common/client.py` → `ct_common/mapping.py` → `commerce_common/fencing.py` |
| 9 | Result appended as a `user`/`tool_result` message, **loop back to step 4** | orchestrator `:257` |

The loop exits when the model returns without a tool call, or when a round of clean
presentation calls closes the turn (`close_on_presentation`).

---

## Trace 1 — "show me some chairs under $1000"

```
[  0.01s] ENTRY     POST /api/chat
[  0.02s] PROMPT    static prompt + 21 tool definitions, identical every turn
[  0.13s] -> CT-AUTH /oauth/token                      115ms
[  0.27s] -> CT      GraphQL FindCart                  135ms
[  2.01s] -> MODEL   /v1/messages                     1704ms
[  2.85s] TOOL-CALL search_products {'query':'chair','filters':{'max_price':1000}}
[  2.97s] -> CT      GraphQL Search                    121ms
[  3.03s] -> CT      REST /product-types?limit=100      53ms
[  3.10s] TOOL-RSLT fenced back to the model
[  4.98s] -> MODEL   /v1/messages                     1875ms
[  8.81s] TOOL-CALL present_products {...5 picks...}
[  8.81s]           -> enriched from session provenance, no platform call
[  8.81s] UI-EVENT  component=products -> SSE to the browser
[ 10.18s] -> MODEL   /v1/messages                     1363ms
[ 10.55s] TOOL-CALL present_suggestions
[ 10.55s] DONE      stop=end_turn  cache_read=31181  in=205 out=617  10535ms
```

### What to say at each point

**The two commercetools calls before the model.** `FindCart` and, later, `product-types` are
**grounding prefetches** — the host reads the cart before the turn so the model's first
message already contains it. Nothing about that is the model's decision.

**"$1000" became a structured filter, not a string.** The model returned
`{'query': 'chair', 'filters': {'max_price': 1000}}`. The schema for that lives in
`shopping_agent/tools/registry.py`; the model is choosing arguments against a contract, not
composing a query.

**The tool name becomes a Python call in one dictionary lookup.** `execution.py:240` —
`handler = self._handlers.get(name)`. There is no dynamic dispatch, no eval, and no network
hop between "the model said `search_products`" and "our function runs". That is the whole
scaffolding, and it is worth showing on screen.

**Then gates run, before commercetools.** `search_products` is a read so it passes, but the
same path for `add_to_cart` checks the provenance gate and the quantity caps *first*. **This
is the sentence that matters:** in this architecture the gate runs before the platform call,
because the tool executes in our process.

**GraphQL Search → typed records.** `ct_shopping/backend.py:145` builds the query;
`ct_common/mapping.py:to_product` turns each projection into a `Product` — deriving options
from what differs across variants, taking `price.discounted.value`, reading name from
`nameAllLocales`. The model never sees a commercetools projection.

**The result is fenced.** `commerce_common/fencing.py` wraps it as untrusted data with a
notice, capped at 12,000 characters. That is why the byte-size work mattered.

**Model call #2 gets the tool result** as a `user` message containing a `tool_result` block
(orchestrator `:257`) — the same conversation, one round longer.

**`present_products` makes no platform call.** The model picks ids and reasons; the server
validates each id against `state.seen_products` and fills in the card from what it already
holds. A `ui` event goes to the browser over SSE. This is why the model cannot invent a
product into the UI.

**`cache_read=31181`** — the static prefix was reused. Non-zero means the prompt bytes did
not change, which is the caching claim being demonstrated live.

---

## Trace 2 — "give me a list of slow selling products from the past week"

```
[  1.14s] -> MODEL   /v1/messages                     1097ms
[  2.33s] TOOL-CALL load_skill {'skill_name': 'inventory-operations'}
[  2.33s]           -> skills registry, no platform call
[  3.65s] -> MODEL   /v1/messages                     1286ms
[  3.93s] TOOL-CALL get_inventory_alerts {}
[  4.07s] -> CT-AUTH /oauth/token                      137ms
[  4.15s] -> CT      REST /inventory?where=availableQuantity < 10&limit=100
[  4.21s] -> CT      REST /inventory?where=sku in ("ALC-01","CST-01",...)
[  4.30s] -> CT      REST /orders?where=createdAt > "2026-08-09..."&limit=500
[  4.40s] -> CT      REST /product-projections/search?filter=variants.sku:"CST-01"
[  4.45s] -> CT      REST /product-projections/search?filter=variants.sku:"RAM-094"   <- these four
[  4.46s] -> CT      REST /product-projections/search?filter=variants.sku:"RMP-01"        overlap
[  4.46s] -> CT      REST /product-projections/search?filter=variants.sku:"GRCG-01"
[  5.62s] -> MODEL   /v1/messages                     1149ms
[  9.67s] TOOL-CALL present_digest {...}
[ 11.55s] DONE      stop=end_turn  cache_read=32907  11523ms
```

### What to say

**Step one was a skill load, not a data call.** The agent decided the request was an
inventory question and pulled `inventory-operations` from the in-process skill registry.
Skills are directories of `SKILL.md` — the flows are Anthropic's, unchanged.

**One tool call, eight commercetools requests.** `get_inventory_alerts` is *derived*:
commercetools has no alerts object. Candidate SKUs from `/inventory`, their true per-channel
quantities, 30 days of orders for sales velocity, then one product lookup per flagged SKU.
Point at the four overlapping timestamps — those run concurrently
(`ct_merchant/backend.py`, `asyncio.gather`).

**The best moment in this trace is the reply:**

> "Only one item is flagged as a true slow mover (the data covers 30 days, not a week — no
> weekly pace is tracked)."

It was asked for *the past week* and said the data is 30 days. That is not politeness — the
backend returns a note saying what the window is, and the agent repeats it instead of
inventing a weekly number. Same discipline as conversion rendering as an em dash.

---

## The proof: every host contacted

Both traces end with this:

```
EVERY HOST THIS TURN CONTACTED
    8 x  api.us-central1.gcp.commercetools.com
    4 x  api.anthropic.com
    1 x  auth.us-central1.gcp.commercetools.com

  MCP endpoints contacted: NONE
```

Three hosts: the Messages API, the commercetools API, and its auth server. **No MCP
endpoint, in either direction.** Reinforce it three ways on camera:

1. `pip list | grep -i mcp` → the package is not installed in the service venv.
2. `grep -rn "mcp_servers\|mcp_toolset\|import mcp" ct_common ct_shopping ct_merchant service` → nothing.
3. The egress list above.

### Why it was built this way — the line to land

> "There's an easier version of this: give the Messages API a commercetools MCP server and
> let the model call it. We had that. The problem is the tool loop runs inside the model
> provider's infrastructure — so by the time your code can check a cart write was scoped to
> the right shopper, the write has already happened. You can block the reply; you can't
> block the write. Here the tool runs in our process, so the provenance gate and the caps
> run *before* commercetools sees anything. That's the whole reason for the extra adapter."

---

## Recording notes

- Terminal font up, window narrow enough that lines don't wrap.
- Run each trace **twice**; use the second take. The first pays for the OAuth token and the
  product-type read, and `cache_read` is 0 on a cold prefix.
- Have `ct_common/mapping.py` and `commerce_common/execution.py:214-245` open in a second
  tab — those two files are where the interesting half of the answer lives.
- Total footage: two traces at ~11s each plus narration is about 8 minutes.
