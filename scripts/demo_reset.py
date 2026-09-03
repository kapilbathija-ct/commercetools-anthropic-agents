"""Put the demo back to a clean starting state.

Clears the staged-change queue and the stored agent sessions, so the portal opens with an
empty "awaiting approval" banner and the shop opens as a new guest. Touches nothing else:
the catalogue, orders, inventory and prices are left exactly as they are.

    python scripts/demo_reset.py            # show what would be cleared
    python scripts/demo_reset.py --confirm  # clear it
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ct_common import load_settings  # noqa: E402
from ct_common.sync_client import CTSyncClient  # noqa: E402
from ct_merchant.ledger import CONTAINER as CHANGES  # noqa: E402
from service.ct_sessions import MESSAGES_CONTAINER, STATE_CONTAINER  # noqa: E402

CONTAINERS = (CHANGES, STATE_CONTAINER, MESSAGES_CONTAINER)


def main(confirm: bool) -> int:
    client = CTSyncClient(load_settings())
    try:
        for container in CONTAINERS:
            page = client.get(f"/custom-objects/{container}", {"limit": 200})
            keys = [obj["key"] for obj in page.get("results") or []]
            if not keys:
                print(f"{container}: already empty")
                continue
            if not confirm:
                print(f"{container}: {len(keys)} object(s) would be cleared")
                continue
            for key in keys:
                client.delete(f"/custom-objects/{container}/{key}")
            print(f"{container}: cleared {len(keys)}")
        if not confirm:
            print("\nnothing changed; re-run with --confirm")
        else:
            print("\nRestart the API too, so an in-process ledger starts empty as well.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main("--confirm" in sys.argv))
