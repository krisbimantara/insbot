"""Redis-backed session store for the Telegram Inspection Bot.

Implements ``RedisSessionStore`` which persists:
- Inspection sessions as JSON strings under ``session:{telegram_id}:{motor_id}``
  with TTL 86400 s (refreshed on every save).
- Pending motor queues as Redis SETs under ``pending:{telegram_id}``
  with TTL 86400 s (refreshed on every mutation).

Serialisation uses Pydantic's ``model_dump_json()`` / ``model_validate_json()``
for a stable, schema-versioned round-trip (Requirement 9.3, 9.4).

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7
"""

from __future__ import annotations

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from bot.adapters.exceptions import SessionNotFound
from bot.domain.models import Session

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------

_SESSION_TTL = 86400  # seconds (Requirement 9.1)
_PENDING_TTL = 86400  # seconds (Requirement 9.2)


def _session_key(telegram_id: str, motor_id: str) -> str:
    return f"session:{telegram_id}:{motor_id}"


def _pending_key(telegram_id: str) -> str:
    return f"pending:{telegram_id}"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class RedisSessionStore:
    """Async Redis-backed store for inspection sessions and pending motor queues.

    Parameters
    ----------
    redis_client:
        An already-created ``redis.asyncio.Redis`` instance (with
        ``decode_responses=True`` so all values come back as ``str``).
    ttl:
        Time-to-live in seconds for both session and pending keys.
        Defaults to 86400 (24 hours).
    """

    def __init__(self, redis_client: aioredis.Redis, ttl: int = _SESSION_TTL) -> None:
        self._redis = redis_client
        self._ttl = ttl

    # ------------------------------------------------------------------
    # Session operations
    # ------------------------------------------------------------------

    async def get_session(self, telegram_id: str, motor_id: str) -> Session | None:
        """Return the ``Session`` for *(telegram_id, motor_id)*, or ``None`` if absent.

        Raises
        ------
        redis.exceptions.RedisError
            Propagated as-is so callers can catch and surface the "busy" message
            (Requirement 9.5).
        """
        key = _session_key(telegram_id, motor_id)
        try:
            raw: str | None = await self._redis.get(key)
        except RedisError:
            raise

        if raw is None:
            return None

        return Session.model_validate_json(raw)

    async def save_session(self, session: Session) -> None:
        """Persist *session* to Redis and refresh its TTL.

        Uses ``SET key value EX ttl`` which is atomic (Requirement 9.4).

        Raises
        ------
        redis.exceptions.RedisError
            Propagated so callers can surface the "busy" message (Requirement 9.5).
        """
        key = _session_key(session.telegram_id, session.motor_id)
        payload = session.model_dump_json()
        try:
            await self._redis.set(key, payload, ex=self._ttl)
        except RedisError:
            raise

    async def delete_session(self, telegram_id: str, motor_id: str) -> None:
        """Delete the session key for *(telegram_id, motor_id)*.

        Idempotent — no error if the key does not exist.

        Raises
        ------
        redis.exceptions.RedisError
            Propagated so callers can surface the "busy" message.
        """
        key = _session_key(telegram_id, motor_id)
        try:
            await self._redis.delete(key)
        except RedisError:
            raise

    # ------------------------------------------------------------------
    # Pending queue operations
    # ------------------------------------------------------------------

    async def add_pending(self, telegram_id: str, motor_id: str) -> None:
        """Add *motor_id* to the pending SET for *telegram_id* and refresh TTL.

        Idempotent: ``SADD`` is a no-op if the member already exists, satisfying
        Requirement 1.8 (duplicate webhook → no duplicate entry).

        Raises
        ------
        redis.exceptions.RedisError
            Propagated so callers can surface the "busy" message.
        """
        key = _pending_key(telegram_id)
        try:
            await self._redis.sadd(key, motor_id)
            await self._redis.expire(key, self._ttl)
        except RedisError:
            raise

    async def remove_pending(self, telegram_id: str, motor_id: str) -> None:
        """Remove *motor_id* from the pending SET for *telegram_id*.

        Idempotent — no error if the member does not exist.

        Raises
        ------
        redis.exceptions.RedisError
            Propagated so callers can surface the "busy" message.
        """
        key = _pending_key(telegram_id)
        try:
            await self._redis.srem(key, motor_id)
        except RedisError:
            raise

    async def replace_pending(self, telegram_id: str, motor_ids: list[str]) -> None:
        """Atomically replace the pending SET with *motor_ids*.

        Uses a pipeline (``MULTI/EXEC``) to ensure the DEL + SADD + EXPIRE
        sequence is atomic from the perspective of other readers (Requirement 3.7).

        If *motor_ids* is empty the key is deleted and not re-created (no TTL
        needed for a non-existent key).

        Raises
        ------
        redis.exceptions.RedisError
            Propagated so callers can surface the "busy" message.
        """
        key = _pending_key(telegram_id)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(key)
                if motor_ids:
                    pipe.sadd(key, *motor_ids)
                    pipe.expire(key, self._ttl)
                await pipe.execute()
        except RedisError:
            raise

    async def list_pending(self, telegram_id: str) -> set[str]:
        """Return the set of pending motor IDs for *telegram_id*.

        Returns an empty set if the key does not exist (TTL expired or never
        created).

        Raises
        ------
        redis.exceptions.RedisError
            Propagated so callers can surface the "busy" message.
        """
        key = _pending_key(telegram_id)
        try:
            members: set[str] = await self._redis.smembers(key)
        except RedisError:
            raise

        return members

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def ping(self) -> bool:
        """Return ``True`` if Redis responds to PING, ``False`` otherwise.

        Used by the ``/healthz`` endpoint (Requirement 11.5).  Swallows
        ``RedisError`` intentionally — a failed ping means Redis is down, which
        is the information the caller needs.
        """
        try:
            await self._redis.ping()
            return True
        except RedisError:
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def create_redis_store(redis_url: str, ttl: int = _SESSION_TTL) -> RedisSessionStore:
    """Create a ``RedisSessionStore`` from a Redis URL.

    Parameters
    ----------
    redis_url:
        A Redis connection URL, e.g. ``redis://localhost:6379/0``.
    ttl:
        TTL in seconds for session and pending keys.  Defaults to 86400.

    Returns
    -------
    RedisSessionStore
        A ready-to-use store backed by a new ``redis.asyncio`` client.
    """
    client: aioredis.Redis = aioredis.from_url(redis_url, decode_responses=True)
    return RedisSessionStore(client, ttl=ttl)
