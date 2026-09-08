# Sequence diagrams

Drawn from the real traces in `WALKTHROUGH.md`, not from the design intent — the round
counts, the call fan-out and the concurrency are what `scripts/trace_turn.py` actually
recorded.

Mermaid renders in GitHub, Notion, Confluence and most slide tools. There is an ASCII
version of the first one at the bottom for a terminal or a plain slide.

---

## 1. Shopping path — "show me some chairs under $1000"

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser<br/>(storefront :3005)
    participant S as Service<br/>service/main.py
    participant A as ShoppingAgent<br/>orchestrator.py
    participant X as Executor + gates<br/>execution.py
    participant K as Backend<br/>ct_shopping/backend.py
    participant C as CTClient<br/>ct_common/client.py
    participant CT as commercetools API
    participant M as Anthropic<br/>Messages API

    B->>S: POST /api/chat {message}
    S->>S: append_user_turn to transcript
    S->>A: stream_turn(messages, session, state)

    Note over A,CT: grounding prefetch — the host reads the cart<br/>before the turn, not the model
    A->>K: get_cart(session)
    K->>C: GraphQL FindCart
    C->>CT: POST /graphql
    CT-->>C: cart
    C-->>K: cart
    K-->>A: Cart

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 1
    A->>M: messages.stream(system=static prompt, tools=21, messages)
    M-->>A: stop_reason=tool_use<br/>tool_use search_products {query chair, max_price 1000}
    end

    A->>X: execute("search_products", args)
    X->>X: handlers[name] lookup — one dict, no network
    X->>X: gates (provenance, caps) run BEFORE the platform
    X->>K: search_products(session, query, filters, limit)
    K->>C: GraphQL Search + price selection
    C->>CT: POST /graphql
    CT-->>C: product projections
    C-->>K: raw projections
    K->>K: mapping.to_product — options from what differs,<br/>discounted price, nameAllLocales, sellable stock
    K-->>X: list[Product]
    X->>X: remember_products (session provenance)
    X->>X: fence result, cap 12000 chars
    X-->>A: ToolOutcome
    A-->>B: SSE tool_call, tool_result

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 2 — tool_result appended as a user message
    A->>M: messages + tool_result block
    M-->>A: tool_use present_products {picks with reasons}
    end

    A->>X: execute("present_products", picks)
    X->>X: validate every id against seen_products,<br/>enrich card — NO platform call
    X-->>A: ui payload
    A-->>B: SSE ui {component products}

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 3
    A->>M: messages + tool_result
    M-->>A: tool_use present_suggestions
    end

    A-->>B: SSE ui {component suggestions}
    A-->>B: SSE text_delta, then turn_complete<br/>cache_read=31181
    Note over B,CT: hosts contacted this turn: api.anthropic.com,<br/>api.commercetools.com, auth.commercetools.com — no MCP
```

**Three things to point at:** the grounding prefetch happens before any model call; the
gates run before commercetools rather than after; and `present_products` never touches the
platform, so the model cannot put a product on screen that the session did not already see.

---

## 2. Merchant read path — "slow selling products from the past week"

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser<br/>(portal :3105)
    participant S as Service<br/>service/merchant.py
    participant A as MerchantAgent<br/>orchestrator.py
    participant X as Executor + gates
    participant K as Backend<br/>ct_merchant/backend.py
    participant CT as commercetools API
    participant M as Anthropic<br/>Messages API

    B->>S: POST /api/merchant/chat {message}
    S->>S: operator read from server-side config,<br/>never from the request
    S->>A: stream_turn(messages, session, state)

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 1
    A->>M: messages.stream(system, tools=21)
    M-->>A: tool_use load_skill {inventory-operations}
    end
    A->>X: execute("load_skill")
    X-->>A: SKILL.md from the in-process registry — no platform call

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 2
    A->>M: messages + tool_result
    M-->>A: tool_use get_inventory_alerts {}
    end

    A->>X: execute("get_inventory_alerts")
    X->>K: get_inventory_alerts(session)
    Note over K,CT: commercetools has no alerts object.<br/>The alert is derived from three sources.
    K->>CT: REST /inventory where availableQuantity under 10
    CT-->>K: candidate skus
    K->>CT: REST /inventory where sku in (...)
    CT-->>K: per-channel quantities
    K->>CT: REST /orders where createdAt within 30d
    CT-->>K: order lines for sales velocity

    par four lookups run concurrently (asyncio.gather)
        K->>CT: /product-projections/search sku CST-01
    and
        K->>CT: /product-projections/search sku RAM-094
    and
        K->>CT: /product-projections/search sku RMP-01
    and
        K->>CT: /product-projections/search sku GRCG-01
    end
    CT-->>K: listings

    K->>K: sellable = channel-less entry;<br/>other_channels reported separately
    K-->>X: list[InventoryAlert] + note "window is 30 days"
    X->>X: fence
    X-->>A: ToolOutcome

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 3
    A->>M: messages + tool_result
    M-->>A: tool_use present_digest
    end
    A-->>B: SSE ui {component digest}

    rect rgb(238, 244, 255)
    Note over A,M: ROUND 4
    A->>M: messages + tool_result
    M-->>A: tool_use present_suggestions, then end_turn
    end
    A-->>B: SSE ui, text_delta, turn_complete

    Note over A,B: the reply says "the data covers 30 days, not a week —<br/>no weekly pace is tracked" rather than inventing one
```

**One tool call, eight commercetools requests, four of them concurrent.** And the answer
corrects the question's premise instead of fabricating a weekly figure.

---

## 3. Merchant write path — propose, approve, apply

The one that answers "what stops it changing prices on its own". Two separate HTTP requests
by design: the agent proposes in one, a human approves in another.

```mermaid
sequenceDiagram
    autonumber
    participant O as Operator<br/>(portal)
    participant S as Service<br/>service/merchant.py
    participant A as MerchantAgent
    participant X as Executor + gates
    participant K as Backend
    participant L as Ledger<br/>Custom Objects
    participant CT as commercetools API
    participant M as Anthropic

    Note over O,CT: REQUEST 1 — the agent proposes. Nothing is written.
    O->>S: POST /api/merchant/chat "restock the serving tray"
    S->>A: stream_turn
    A->>M: round 1
    M-->>A: tool_use stage_inventory_action
    A->>X: execute("stage_inventory_action")
    X->>K: stage_inventory_action(items)
    K->>CT: read current stock and other-channel units
    CT-->>K: quantities
    K->>K: check_guardrails — 500-unit cap, 25 items per change
    K->>L: persist StagedChange status=staged
    K-->>X: StagedChange + note "160 units in other channels"
    X-->>A: ToolOutcome
    A-->>O: SSE ui {component change_preview}<br/>Approve / Dismiss / "Nothing applies until you approve"

    Note over O,CT: REQUEST 2 — the human approves. Only now is anything written.
    O->>S: POST /api/merchant/changes/{id}/apply
    S->>S: mark id approved for THIS call only
    S->>X: execute("apply_change", {change_id})
    X->>X: gate — id must be in seen_changes AND in approved_change_ids
    X->>K: apply_change(change_id)
    K->>L: require status=staged
    L-->>K: StagedChange
    K->>K: re-check guardrails under the current config
    K->>CT: POST /inventory/{id} addQuantity
    CT-->>K: 200
    K->>L: mark applied, stamp applied_by
    K-->>X: StagedChange status=applied
    X-->>S: change_update
    S->>S: clear the approval mark, whatever the outcome
    S-->>O: {ok true, change}

    Note over S,K: the staged check and the guardrails run BEFORE the write.<br/>They used to run after, so applying twice wrote twice.
```

---

## ASCII version of diagram 1, for a plain slide

```
Browser        Service         Agent          Executor       Backend        commercetools   Anthropic
   |              |              |                |              |                |             |
   |--POST chat-->|              |                |              |                |             |
   |              |--stream_turn>|                |              |                |             |
   |              |              |--get_cart----->|------------->|--GraphQL------>|             |
   |              |              |<-------------------------------Cart------------|             |
   |              |              |                |              |                |             |
   |              |              |==== ROUND 1 : system + 21 tools =============================>|
   |              |              |<=== tool_use search_products {chair, max 1000} ===============|
   |              |              |                |              |                |             |
   |              |              |--execute------>| handlers[name]|                |             |
   |              |              |                | gates FIRST   |                |             |
   |              |              |                |--search------>|--GraphQL------>|             |
   |              |              |                |               |<--projections--|             |
   |              |              |                |               | to_product()   |             |
   |              |              |                |<--[Product]---|                |             |
   |              |              |                | provenance + fence             |             |
   |<--SSE tool_call/result------|<---------------|               |                |             |
   |              |              |                |              |                |             |
   |              |              |==== ROUND 2 : + tool_result ================================>|
   |              |              |<=== tool_use present_products ==============================|
   |              |              |--execute------>| validate ids, enrich (NO platform call)      |
   |<--SSE ui {products}---------|<---------------|                                              |
   |              |              |==== ROUND 3 ================================================>|
   |              |              |<=== present_suggestions, end_turn ==========================|
   |<--SSE ui + text + turn_complete (cache_read=31181)                                          |
   |              |              |                |              |                |             |

hosts contacted: api.anthropic.com | api.commercetools.com | auth.commercetools.com
                 no MCP endpoint, in either direction
```
