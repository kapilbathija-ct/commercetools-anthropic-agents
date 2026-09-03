"""Create the Type this project's carts carry.

It exists for one reason. This commercetools project holds fourteen leftover ``ext-lab-*``
API Extensions whose trigger conditions read ``custom(fields(extLabTest = "..."))`` with no
``is defined`` guard. A predicate like that cannot be evaluated against a cart that has no
custom object at all, so **every** cart Update in the project fails with
``ExtensionPredicateEvaluationFailed`` unless the cart carries a custom object with that
field. Giving our carts one field, set to a value none of those conditions match, makes
each predicate evaluate to false and no extension fires.

The alternative -- deleting extensions that belong to someone else's test setup in a shared
lab -- is not ours to do.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ct_common import CTClient, load_settings  # noqa: E402
from ct_shopping.backend import CART_TYPE_FIELD, CART_TYPE_KEY, CART_TYPE_VALUE  # noqa: E402


async def main(live: bool) -> None:
    client = CTClient(load_settings())
    try:
        existing = await client.get(f"/types/key={CART_TYPE_KEY}")
        if existing:
            names = [f["name"] for f in existing.get("fieldDefinitions", [])]
            print(f"{CART_TYPE_KEY} already exists (fields: {', '.join(names)})")
            return
        draft = {
            "key": CART_TYPE_KEY,
            "name": {"en": "Agent cart marker"},
            "description": {
                "en": (
                    "Carried by carts the commerce agents create. The single field exists so "
                    "the project's unguarded ext-lab-* extension predicates can be evaluated; "
                    "its value matches none of their conditions."
                )
            },
            # In commercetools one "order" resourceTypeId covers both Cart and Order.
            "resourceTypeIds": ["order"],
            "fieldDefinitions": [
                {
                    "name": CART_TYPE_FIELD,
                    "label": {"en": "Extension lab marker"},
                    "type": {"name": "String"},
                    "required": False,
                    "inputHint": "SingleLine",
                }
            ],
        }
        if not live:
            print(f"would create Type {CART_TYPE_KEY} with field {CART_TYPE_FIELD}")
            return
        created = await client.post("/types", draft)
        print(
            f"created Type {created['key']} (version {created['version']}) "
            f"with field {CART_TYPE_FIELD}={CART_TYPE_VALUE!r}"
        )
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main("--live" in sys.argv))
