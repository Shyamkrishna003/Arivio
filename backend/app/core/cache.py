"""
Redis JSON cache.

Used for responses we fetch from third parties and must not fetch again on
every keystroke — currently Open Food Facts name searches, which are slower
and more rate-limited than its barcode lookup.

Every operation degrades to a miss rather than an error. A cache is an
optimisation, and a search must still work when Redis is down or was never
started (Option B in the README brings up Postgres and Redis together, but
nothing enforces that).
"""

import json
from typing import Any, Optional

from app.core.config import get_settings

settings = get_settings()

_client = None
_unavailable = False


def _get_client():
    """
    Lazily connect.

    Built on first use rather than at import so the module can be imported in
    tests and scripts that never touch Redis. After a connection failure the
    client is not rebuilt on every call — `_unavailable` latches, so a dead
    Redis costs one failed connection per process rather than one per request.
    """
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is None:
        try:
            from redis import asyncio as aioredis
            _client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as e:  # noqa: BLE001 — never let cache setup break a request
            print(f"⚠️ Redis unavailable, continuing without cache: {e}")
            _unavailable = True
            return None
    return _client


async def get_json(key: str) -> Optional[Any]:
    """Read a cached value, or None on a miss, a dead cache, or corrupt JSON."""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Cache read failed for {key}: {e}")
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Written by an older version of the code with a different shape.
        # Treat as a miss; the write below will overwrite it.
        return None


async def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    """Cache a value. Failures are swallowed — the caller already has its data."""
    client = _get_client()
    if client is None:
        return
    try:
        await client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Cache write failed for {key}: {e}")


async def close() -> None:
    """
    Release the connection pool.

    The server never needs this — the pool lives as long as the process. Short
    scripts do: without it redis-py closes the connection from __del__ during
    interpreter shutdown, after the event loop has gone, and prints an
    alarming "Event loop is closed" traceback over whatever the script was
    actually reporting.
    """
    global _client
    if _client is None:
        return
    try:
        await _client.aclose()
    except Exception:  # noqa: BLE001 — shutting down anyway
        pass
    _client = None
