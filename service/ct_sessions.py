"""The session store over commercetools Custom Objects.

Serverless has no instance affinity, so the reference's in-process store loses a session
between the request that starts it and the next one. The usual answer is Redis. This uses
Custom Objects instead, for three reasons that are specific rather than clever:

* **The compare-and-set is already there.** ``write_state`` must refuse a write whose
  version is behind the stored one, and a Custom Object write carrying a stale ``version``
  is refused with ``409 ConcurrentModification`` -- verified live. That is the exact
  semantic, enforced by the platform rather than reimplemented in a Lua script.
* **No new vendor, no new secret.** The credentials are already in the process.
* **Room to spare.** A value of at least 1 MB is accepted (verified at 16, 64, 256, 512 and
  1024 KB); a compacted transcript is far smaller.

The cost is a commercetools round trip per session read and write, which is slower than
Redis and puts transcripts in the commerce project. For a shared demo that is the right
trade; a busier deployment should use ``RedisSessionStore``, which implements the same six
methods.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from ct_common import CTError
from ct_common.sync_client import CTSyncClient

from .sessions import SessionConflictError, SessionStore

StateT = TypeVar("StateT", bound=BaseModel)

STATE_CONTAINER = "agent-session-state"
MESSAGES_CONTAINER = "agent-session-messages"
# Kept inside the state value rather than beside it; see write_state.
MESSAGE_COUNT_KEY = "_message_count"


class CustomObjectSessionStore(SessionStore[StateT], Generic[StateT]):
    """The reference's six storage methods over Custom Objects, synchronously -- which is
    what the contract asks for, and what FastAPI's threadpool is there to absorb."""

    def __init__(self, state_type: type[StateT], client: CTSyncClient) -> None:
        super().__init__(state_type)
        self._client = client
        # The version the platform actually holds after the most recent write, per session.
        self._written_version: dict[str, int] = {}
        # The transcript length being written, so the state value changes when it does.
        self._pending_count: dict[str, int] = {}

    def save(self, record: Any) -> None:
        """The reference's ``save`` plus one correction that is load-bearing.

        ``save`` assumes every ``write_state`` advances the stored version by exactly one,
        which is true of its own in-memory store. commercetools advances a Custom Object's
        version **only when the value actually changes** -- rewriting an identical value is
        a no-op that returns the same version. So a turn that grew the transcript without
        changing the state document left ``record.version`` one ahead of reality, and every
        later compare-and-set then compared the wrong number: stale writes were accepted
        and the guarantee was silently gone. Adopting the platform's own version afterwards
        keeps the two in step, at no extra round trip.
        """
        self._pending_count[record.session_id] = len(record.messages)
        try:
            super().save(record)
        finally:
            self._pending_count.pop(record.session_id, None)
        actual = self._written_version.pop(record.session_id, None)
        if actual is not None:
            record.version = actual

    def read_state(self, session_id: str) -> tuple[int, dict[str, Any]] | None:
        stored = self._client.get(f"/custom-objects/{STATE_CONTAINER}/{session_id}")
        if not stored:
            return None
        # The Custom Object's own version is the session's version, so the compare-and-set
        # below is the platform's rather than ours. The message-count marker is stripped so
        # the caller's view of the document matches what ``state_document()`` produces and
        # the reference's own change detection keeps working.
        value = {k: v for k, v in stored["value"].items() if k != MESSAGE_COUNT_KEY}
        return stored["version"], value

    def write_state(self, session_id: str, document: dict[str, Any], version: int) -> None:
        # The transcript length travels inside the state value on purpose. commercetools
        # advances a version only when the value changes, so a turn that appended messages
        # without touching the state document would write an identical value, get a no-op,
        # and leave the version where it was -- which means no compare-and-set at all for
        # exactly the write that matters. Two concurrent turns then both wrote the
        # transcript and one was silently lost (observed: a turn disappeared). Folding the
        # count in makes every transcript growth a real value change, so the version moves
        # and the loser of the race is refused.
        body: dict[str, Any] = {
            "container": STATE_CONTAINER,
            "key": session_id,
            "value": {**document, MESSAGE_COUNT_KEY: self._pending_count.get(session_id, 0)},
        }
        # Version 0 means the session is being started: no object exists yet, so no version
        # is sent. Any later write carries the loaded version and is refused if it is stale.
        if version:
            body["version"] = version
        try:
            written = self._client.post("/custom-objects", body)
        except CTError as error:
            if error.code == "ConcurrentModification" or error.status == 409:
                raise SessionConflictError(session_id) from error
            raise
        self._written_version[session_id] = written["version"]

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        stored = self._client.get(f"/custom-objects/{MESSAGES_CONTAINER}/{session_id}")
        if not stored:
            return []
        # Stored as one JSON string rather than a nested structure: a transcript holds tool
        # results whose shapes are not worth asking a Custom Object value to model.
        return json.loads(stored["value"]["messages"])

    def write_messages(self, session_id: str, messages: list[dict[str, Any]], start: int) -> None:
        """No version is sent here, so the transcript has no compare-and-set of its own.
        It does not need one: ``save`` writes the state document first and under CAS, so a
        writer that lost the race is refused before it reaches this call."""
        existing = self.read_messages(session_id) if start else []
        combined = existing[:start] + messages
        self._client.post(
            "/custom-objects",
            {
                "container": MESSAGES_CONTAINER,
                "key": session_id,
                "value": {"messages": json.dumps(combined)},
            },
        )

    def delete(self, session_id: str) -> None:
        for container in (STATE_CONTAINER, MESSAGES_CONTAINER):
            # Already gone is the desired end state.
            with contextlib.suppress(CTError):
                self._client.delete(f"/custom-objects/{container}/{session_id}")

    def session_ids_for_user(self, user_id: str) -> list[str]:
        page = self._client.get(
            f"/custom-objects/{STATE_CONTAINER}",
            {"where": f'value(user_id="{user_id}")', "limit": 50},
        )
        return [obj["key"] for obj in page.get("results") or []]
