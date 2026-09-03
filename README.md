# commercetools-anthropic-agents

A shopping agent and a merchant agent for commercetools, built on
[`anthropics/commerce-agents`](https://github.com/anthropics/commerce-agents).

The agents reach commercetools through a backend this project owns, and their tools run in
this process. That is deliberate: the gates that matter -- provenance on every cart write,
quantity caps, fencing of everything the platform returns -- run *before* the commercetools
call, and the shopper's identity never enters the model's context at all.

## Run it

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # commercetools credentials + an org ANTHROPIC_API_KEY
uvicorn service.main:app --reload --port 8000
```

```
POST /api/session   start a session; a customer id makes it a member, absent makes it a guest
POST /api/chat      one turn, streamed as SSE agent events   (X-Session-Id)
GET  /api/cart      the session's cart                        (X-Session-Id)
POST /api/reset     drop the session                          (X-Session-Id)
GET  /api/health    project and store
```

## Layout

| Path | What it is |
|---|---|
| `ct_common/` | commercetools settings, the authenticated client, the reference cache, the domain errors, the projection mappers |
| `ct_shopping/` | `CommercetoolsStorefront`, config, executor subclass, the five flows, tests |
| `ct_merchant/` | the merchant agent's flows; the backend is not written yet |
| `service/` | the FastAPI service, the session store and its Redis subclass |
| `scripts/` | `probe_backend.py`, `smoke_chat.py`, `check_extensions.py`, `seed_policies.py`, `ensure_cart_type.py` |

`CLAUDE.md` carries the decision record: what each backend method maps onto, what the live
catalogue's data turned out to be, and the platform gotchas that shaped the code.

## Verify

```bash
ruff check . && ruff format --check . && pytest      # no network
python scripts/probe_backend.py                      # every backend method, live
python scripts/smoke_chat.py                         # one live conversation
```
