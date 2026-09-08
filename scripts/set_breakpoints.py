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

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / ".venv/lib/python3.13/site-packages"
STORAGE = Path.home() / "Library/Application Support/Code/User/workspaceStorage"

# (label, file, the substring whose line we want, what to look at when it pauses)
WANTED = [
    ("1 entry", ROOT / "service/main.py", "async def chat", "request.message"),
    ("2 model call", SITE / "shopping_agent_runtime/orchestrator.py", "client.messages.stream",
     "request: system, tools (21), messages -- and no mcp_servers"),
    ("3 dispatch", SITE / "commerce_common/execution.py", "handler = self._handlers.get(name)",
     "name, tool_input, handler"),
    ("4 our backend", ROOT / "ct_shopping/backend.py", "async def search_products",
     "query, filters.max_price"),
    ("5 commercetools", ROOT / "ct_common/client.py", "async def graphql", "query, variables"),
    ("6 mapping", ROOT / "ct_common/mapping.py", "\ndef to_product", "projection in, Product out"),
    ("7 result back", SITE / "shopping_agent_runtime/orchestrator.py",
     "tool_result_block(block.id, outcome)", "the tool_result blocks"),
    # the merchant path's two extra stops, disabled so they stay out of the shopping demo
    ("8 merchant backend", ROOT / "ct_merchant/backend.py", "async def get_inventory_alerts",
     "the per-channel stock problem", False),
    ("9 merchant model", SITE / "merchant_agent_runtime/orchestrator.py",
     "client.messages.stream", "same shape, different tools", False),
]


def line_of(path: Path, needle: str) -> int | None:
    """1-indexed line holding ``needle``, or None. A leading newline anchors to column 0."""
    if not path.exists():
        return None
    text = path.read_text()
    at = text.find(needle)
    if at < 0:
        return None
    return text.count("\n", 0, at) + (2 if needle.startswith("\n") else 1)


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
    for label, path, needle, watch, *rest in WANTED:
        enabled = rest[0] if rest else True
        line = line_of(path, needle)
        if line is None:
            missing.append((label, path))
            continue
        resolved.append((label, path, line, watch, enabled))
        flag = "" if enabled else "   (disabled)"
        try:
            shown = path.relative_to(ROOT)
        except ValueError:
            shown = path
        print(f"  {label:<20} {shown}:{line}{flag}")
        print(f"  {'':<20}   look at: {watch}")

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
            "condition": None,
            "hitCondition": None,
            "logMessage": None,
            "uri": uri_json(path),
        }
        for _, path, line, _, enabled in resolved
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
