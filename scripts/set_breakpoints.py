"""Write the walkthrough's breakpoints into VS Code's workspace database.

VS Code keeps source breakpoints in its own per-workspace store, not in a file in the
repo, so there is no supported way to commit them. This writes them directly.

**Quit VS Code before running this.** A running window holds the breakpoint list in
memory and writes it back on exit, which would undo this.

The lines are found by pattern, not hardcoded, so a re-pin of the reference packages moves
them automatically. Run with --check to see where they resolve without writing.

    python scripts/set_breakpoints.py --check
    python scripts/set_breakpoints.py
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / ".venv/lib/python3.13/site-packages"
STORAGE = Path.home() / "Library/Application Support/Code/User/workspaceStorage"


class B(NamedTuple):
    label: str
    path: Path
    needle: str
    watch: str
    enabled: bool = True
    condition: str | None = None


# (label, file, the substring whose line we want, what to look at when it pauses)
WANTED = [
    # Anchor on the first *executable statement* of a function, never the `def` line: a
    # `def` runs when the module (or the enclosing function) is imported, so a breakpoint
    # there fires during app startup and the server never reaches the point of binding.
    #
    # Fields: label, file, unique needle, what to inspect, enabled=True, condition=None.
    B(
        "1 entry",
        ROOT / "service/main.py",
        "append_user_turn(record, request.message",
        "request.message",
    ),
    B(
        "2 model call",
        SITE / "shopping_agent_runtime/orchestrator.py",
        "client.messages.stream",
        'list(request.keys()) / "mcp_servers" in request / len(request["tools"])',
    ),
    # Every tool passes through dispatch's first line. The handler lookup below is reached
    # only by backend tools -- a presentation tool returns two branches earlier -- so
    # without this stop present_products never pauses.
    B(
        "3 dispatch entry",
        SITE / "commerce_common/execution.py",
        "tool_input, _status = self.split_status(name, tool_input)",
        "name, tool_input",
    ),
    B(
        "4 handler lookup",
        SITE / "commerce_common/execution.py",
        "handler = self._handlers.get(name)",
        "handler -- a bound method of our own class",
    ),
    B(
        "5 our backend",
        ROOT / "ct_shopping/backend.py",
        "platform_filters = self._price_filter(filters)",
        "query, filters",
    ),
    # Conditioned, because this one function serves every document in graphql.py. The
    # storefront's own page load fires Browse and FindCart through here dozens of times
    # before the shopper types anything.
    B(
        "6 commercetools",
        ROOT / "ct_common/client.py",
        "response = await self._http.post(\n            self._settings.graphql_url,",
        "variables -- price selection and locale",
        condition='"query Search" in query',
    ),
    # Off by default: fires once per product, so ~100 times during the page load's
    # catalogue index. Tick it on once you are paused at the commercetools call.
    B(
        "7 mapping",
        ROOT / "ct_common/mapping.py",
        'product_id = projection["id"]',
        'len(projection["variants"]) / len(master prices) / attribute_types',
        enabled=False,
    ),
    B(
        "8 result back",
        SITE / "shopping_agent_runtime/orchestrator.py",
        "tool_result_block(block.id, outcome)",
        "block.name, outcome",
    ),
    # the merchant path's two extra stops, disabled so they stay out of the shopping demo
    B(
        "9 merchant backend",
        ROOT / "ct_merchant/backend.py",
        "async def load() -> list[InventoryAlert]:",
        "the per-channel stock problem",
        enabled=False,
    ),
    B(
        "10 merchant model",
        SITE / "merchant_agent_runtime/orchestrator.py",
        "client.messages.stream",
        "same shape, different tools",
        enabled=False,
    ),
]


def line_of(path: Path, needle: str) -> int | None:
    """1-indexed line where ``needle`` starts, or None when it is absent or ambiguous.
    A needle may span lines, which is how a statement that appears twice is pinned down."""
    if not path.exists():
        return None
    text = path.read_text()
    if text.count(needle) != 1:
        return None
    return text.count("\n", 0, text.index(needle)) + 1


def workspace_db() -> Path:
    """The state.vscdb whose workspace.json points at this repo."""
    target = ROOT.as_uri()
    for candidate in sorted(STORAGE.glob("*/workspace.json")):
        folder = json.loads(candidate.read_text()).get("folder")
        # compare resolved paths: the stored URI may or may not have a trailing slash
        if folder and folder.rstrip("/") == target.rstrip("/"):
            return candidate.parent / "state.vscdb"
    raise SystemExit(
        f"no VS Code workspace found for {ROOT}.\nOpen the folder in VS Code once, then re-run."
    )


def uri_json(path: Path) -> dict:
    return {
        "$mid": 1,
        "fsPath": str(path),
        "external": path.as_uri(),
        "path": str(path),
        "scheme": "file",
    }


def main(check: bool) -> int:
    resolved, missing = [], []
    for label, path, needle, watch, enabled, condition in WANTED:
        line = line_of(path, needle)
        if line is None:
            missing.append((label, path))
            continue
        resolved.append((label, path, line, watch, enabled, condition))
        flag = "" if enabled else "   (disabled)"
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"  {label:<20} {shown}:{line}{flag}")
        print(f"  {'':<20}   look at: {watch}")
        if condition:
            print(f"  {'':<20}   only when: {condition}")

    for label, path in missing:
        print(f"  {label:<20} NOT FOUND in {path}", file=sys.stderr)
    if missing:
        print("\nthe reference packages may have moved; nothing written", file=sys.stderr)
        return 1

    if check:
        print("\n--check only; nothing written")
        return 0

    payload = [
        {
            "id": str(uuid.uuid4()),
            "enabled": enabled,
            "lineNumber": line,
            "column": None,
            "condition": condition,
            "hitCondition": None,
            "logMessage": None,
            "uri": uri_json(path),
        }
        for _, path, line, _, enabled, condition in resolved
    ]

    db = workspace_db()
    backup = db.with_suffix(".vscdb.before-breakpoints")
    shutil.copy2(db, backup)
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO ItemTable (key, value) VALUES ('debug.breakpoint', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(payload),),
        )
        con.commit()
    finally:
        con.close()

    print(f"\nwrote {len(payload)} breakpoints to {db.name} (backup: {backup.name})")
    print("Open VS Code now. If it was already open, quit it first and re-run this.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--check" in sys.argv))
