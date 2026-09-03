"""A TTL cache for the project's reference data.

commercetools Expert Services' own account reviews found the same thing at customer after
customer: the top source of redundant API traffic is re-fetching Product Types,
Categories, Channels, Shipping Methods, States, Cart Discounts and config-backing Custom
Objects on every request. Every one of those is needed here -- Product Types decide which
attributes are options, Custom Objects hold the policy passages -- so they are read once
and held.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

DEFAULT_TTL_S = 600.0


class ReferenceCache:
    def __init__(self, ttl_s: float = DEFAULT_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._entries: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str, load: Callable[[], Awaitable[Any]]) -> Any:
        """Return the cached value, loading it once even when several turns race for it."""
        entry = self._entries.get(key)
        if entry and time.monotonic() < entry[0]:
            return entry[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if entry and time.monotonic() < entry[0]:
                return entry[1]
            value = await load()
            self._entries[key] = (time.monotonic() + self._ttl_s, value)
            return value

    def invalidate(self, key: str | None = None) -> None:
        """Drop one key, or everything after a merchant write applied."""
        if key is None:
            self._entries.clear()
        else:
            self._entries.pop(key, None)
