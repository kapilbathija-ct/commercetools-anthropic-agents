"""Report API Extensions whose trigger condition breaks cart writes.

A condition that reads ``custom(fields(...))`` without an ``is defined`` guard cannot be
evaluated against a cart that has no custom object. While one is registered, every cart
Update in the project fails with ``ExtensionPredicateEvaluationFailed`` -- for this agent
and for anything else that writes a cart, including the storefront.

Exit code 0 means cart writes need no workaround, so ``CT_CART_CUSTOM_MARKER`` can stay
off. Exit code 1 lists the extensions to guard or remove.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ct_common import CTClient, load_settings  # noqa: E402


def unguarded(condition: str | None) -> bool:
    if not condition:
        return False
    lowered = condition.lower()
    return "custom(" in lowered and "is defined" not in lowered


async def main() -> int:
    client = CTClient(load_settings())
    try:
        payload = await client.get("/extensions", {"limit": 100})
        offenders: list[tuple[str, str, str]] = []
        total = 0
        for extension in payload.get("results", []):
            total += 1
            for trigger in extension.get("triggers", []):
                if trigger.get("resourceTypeId") != "cart":
                    continue
                condition = trigger.get("condition")
                if unguarded(condition):
                    offenders.append(
                        (
                            extension.get("key") or extension["id"],
                            trigger["resourceTypeId"],
                            condition,
                        )
                    )
        print(f"{total} extensions registered")
        if not offenders:
            print(
                "no unguarded cart condition: cart writes are clean, "
                "leave CT_CART_CUSTOM_MARKER off"
            )
            return 0
        print(
            f"{len(offenders)} unguarded cart condition(s) -- "
            "every cart Update in the project fails:"
        )
        for key, resource, condition in offenders:
            print(f"  {key}  ({resource} Update)  condition={condition}")
        return 1
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
