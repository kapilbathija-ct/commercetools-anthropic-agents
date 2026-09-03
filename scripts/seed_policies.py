"""Seed the policy passages commercetools has no system for.

Writes one Custom Object per passage into the ``policy-content`` container. The container
is new, so nothing already in the project is touched. Re-running overwrites the same keys.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ct_common import CTClient, load_settings  # noqa: E402
from ct_shopping.backend import POLICY_CONTAINER  # noqa: E402


async def main(dry_run: bool) -> None:
    settings = load_settings()
    client = CTClient(settings)
    passages = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "policy-content.json").read_text()
    )
    try:
        for passage in passages:
            key = passage.pop("key")
            if dry_run:
                print(f"would write {POLICY_CONTAINER}/{key}: {passage['title']}")
                continue
            await client.post(
                "/custom-objects",
                {"container": POLICY_CONTAINER, "key": key, "value": passage},
            )
            print(f"wrote {POLICY_CONTAINER}/{key}: {passage['title']}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main("--live" not in sys.argv))
