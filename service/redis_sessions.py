"""The session store over Redis.

Only the six storage methods change; the record, the version check and the request
dependency are the reference's. They are synchronous by contract, so this uses the
blocking client -- FastAPI runs a sync dependency and a sync background task in its
threadpool, which is where these calls belong.

The compare-and-set that ``write_state`` promises has to stay atomic across processes, so
it runs as a Lua script rather than a read-then-write: two requests on one session would
otherwise both observe the same version and one would silently overwrite the other's turn.

Without ``REDIS_URL`` the process falls back to the in-memory store, which is correct for
tests and for a single worker only. The previous build on this project kept conversation
history in a process-local dict and recorded the consequence as its first fragility --
state resets on redeploy and splits across instances.
"""

from __future__ import annotations

import json
from typing import Any, Generic, TypeVar

import redis
from pydantic import BaseModel

from .sessions import SessionConflictError, SessionStore

StateT = TypeVar("StateT", bound=BaseModel)

# Store the document only if the stored version is still the one the caller loaded.
# KEYS[1] state hash, KEYS[2] per-user index; ARGV[1] expected version, ARGV[2] document,
# ARGV[3] user id, ARGV[4] ttl seconds.
_CAS_SCRIPT = """
local current = redis.call('HGET', KEYS[1], 'version')
local expected = tonumber(ARGV[1])
if current == false then current = 0 else current = tonumber(current) end
if current ~= expected then return -1 end
local next_version = expected + 1
redis.call('HSET', KEYS[1], 'version', next_version, 'document', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('SADD', KEYS[2], ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[4])
return next_version
"""

DEFAULT_TTL_S = 60 * 60 * 24


class RedisSessionStore(SessionStore[StateT], Generic[StateT]):
    def __init__(
        self,
        state_type: type[StateT],
        url: str,
        *,
        prefix: str = "commerce-agent",
        ttl_s: int = DEFAULT_TTL_S,
    ) -> None:
        super().__init__(state_type)
        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._prefix = prefix
        self._ttl_s = ttl_s
        self._cas = self._redis.register_script(_CAS_SCRIPT)

    def _state_key(self, session_id: str) -> str:
        return f"{self._prefix}:state:{session_id}"

    def _messages_key(self, session_id: str) -> str:
        return f"{self._prefix}:messages:{session_id}"

    def _user_key(self, user_id: str) -> str:
        return f"{self._prefix}:user:{user_id}"

    # -- The six storage methods ---------------------------------------------------------

    def read_state(self, session_id: str) -> tuple[int, dict[str, Any]] | None:
        stored = self._redis.hgetall(self._state_key(session_id))
        if not stored:
            return None
        return int(stored["version"]), json.loads(stored["document"])

    def write_state(self, session_id: str, document: dict[str, Any], version: int) -> None:
        result = self._cas(
            keys=[self._state_key(session_id), self._user_key(document["user_id"])],
            args=[version, json.dumps(document), document["user_id"], self._ttl_s],
        )
        if int(result) == -1:
            raise SessionConflictError(session_id)

    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        raw = self._redis.lrange(self._messages_key(session_id), 0, -1)
        return [json.loads(item) for item in raw]

    def write_messages(self, session_id: str, messages: list[dict[str, Any]], start: int) -> None:
        key = self._messages_key(session_id)
        pipe = self._redis.pipeline()
        if start == 0:
            # A turn that compacted the transcript rewrites it whole.
            pipe.delete(key)
        else:
            pipe.ltrim(key, 0, start - 1)
        if messages:
            pipe.rpush(key, *[json.dumps(message) for message in messages])
        pipe.expire(key, self._ttl_s)
        pipe.execute()

    def delete(self, session_id: str) -> None:
        self._redis.delete(self._state_key(session_id), self._messages_key(session_id))

    def session_ids_for_user(self, user_id: str) -> list[str]:
        """The per-user index holds user ids against session ids, so this scans the index
        set rather than every session document."""
        return [
            session_id
            for session_id in self._redis.smembers(self._user_key(user_id))
            if self._redis.exists(self._state_key(session_id))
        ]


def build_session_store(state_type: type[StateT], url: str | None) -> SessionStore[StateT]:
    """The Redis store when a URL is configured, the in-process one otherwise."""
    if url:
        return RedisSessionStore(state_type, url)
    return SessionStore(state_type)
