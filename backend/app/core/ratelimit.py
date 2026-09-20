"""
Rate limiting for the authentication endpoints.

Login and registration were unthrottled: a hundred password guesses cost the
same as one, and accounts could be created in a loop. Everything else in the
API needs a valid token first, so these two endpoints are the whole
unauthenticated write surface and are worth protecting on their own rather
than through a global middleware.

Counting is a fixed window in Redis — INCR plus EXPIRE on first write, which
is two commands and needs no coordination between instances. A fixed window
lets through up to twice the limit across a boundary; that matters for billing
meters and not for "is someone grinding passwords", which is what this is for.

**Failure is open, deliberately.** Redis is optional in this deployment
(see cache.py), and a cache outage must not become an authentication outage.
When Redis is unavailable the limiter falls back to an in-process counter,
which still bounds a single instance — the app runs as one container, so in
practice that is the same protection with a different persistence story.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status

from app.core.cache import _get_client


@dataclass(frozen=True)
class Limit:
    """A number of attempts allowed per window of seconds."""
    attempts: int
    window_seconds: int


# Ten guesses per quarter hour is invisible to someone who mistyped their
# password and ruinous to someone working through a word list.
LOGIN_LIMIT = Limit(attempts=10, window_seconds=15 * 60)
# Registration is a one-off for a real user, so this only bites automation.
REGISTER_LIMIT = Limit(attempts=5, window_seconds=60 * 60)
# Refresh is called automatically by the client on a 401, so it needs more
# headroom than a human-driven endpoint.
REFRESH_LIMIT = Limit(attempts=60, window_seconds=15 * 60)


# ── In-process fallback ──
# Maps key -> (count, window_started_at). Only consulted when Redis is down.
_local: dict[str, tuple[int, float]] = {}
# Bounds memory if a flood of distinct keys arrives while Redis is unavailable:
# past this many entries the expired ones are swept before inserting more.
_LOCAL_MAX_KEYS = 10_000

# cache.py builds its client lazily and without connecting, so a dead Redis is
# not discovered until the first command — which means every single auth
# request would report the same outage. Latched so it is said once per process
# and the fallback is then silent.
_redis_failed = False


def _local_hit(key: str, limit: Limit) -> int:
    now = time.monotonic()
    count, started = _local.get(key, (0, now))

    if now - started >= limit.window_seconds:
        count, started = 0, now

    count += 1
    if len(_local) > _LOCAL_MAX_KEYS:
        for k, (_, s) in list(_local.items()):
            if now - s >= limit.window_seconds:
                _local.pop(k, None)

    _local[key] = (count, started)
    return count


async def _hit(key: str, limit: Limit) -> int:
    """Register one attempt against `key`, returning the count in this window."""
    global _redis_failed

    client = None if _redis_failed else _get_client()
    if client is None:
        return _local_hit(key, limit)

    try:
        count = await client.incr(key)
        if count == 1:
            # Only on the first hit: re-expiring on every request would slide
            # the window forward and make the limit unreachable under load.
            await client.expire(key, limit.window_seconds)
        return int(count)
    except Exception as e:  # noqa: BLE001 — a cache fault must not block login
        _redis_failed = True
        print(
            f"⚠️ Redis unavailable for rate limiting ({e}). "
            "Falling back to per-process counters for the rest of this process."
        )
        return _local_hit(key, limit)


def client_ip(request: Request) -> str:
    """
    Best-effort client address.

    X-Forwarded-For is a list the client can prepend to, and this app sits
    behind two proxies it does not control the header handling of, so the
    leftmost entry is NOT trustworthy — an attacker can rotate it freely and
    defeat a per-IP limit. It is used anyway because it still raises the cost
    for unsophisticated abuse, and because the per-account limit below is the
    one that actually protects a targeted account.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce(
    scope: str,
    identifier: str,
    limit: Limit,
    *,
    detail: str = "Too many attempts. Please wait a few minutes and try again.",
) -> None:
    """
    Count one attempt and raise 429 once `limit` is exceeded.

    `scope` separates counters that would otherwise collide (an IP counted for
    login must not also consume its registration budget).
    """
    key = f"ratelimit:{scope}:{identifier}"
    count = await _hit(key, limit)
    if count > limit.attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": str(limit.window_seconds)},
        )


async def enforce_login(request: Request, email: Optional[str]) -> None:
    """
    Throttle a login attempt on both the source address and the account.

    The per-account counter is the important half: it is keyed on something
    the attacker must hold fixed to make progress, whereas the source address
    is something they can change at will.
    """
    await enforce("login-ip", client_ip(request), LOGIN_LIMIT)
    if email:
        await enforce(
            "login-account",
            email.strip().lower(),
            LOGIN_LIMIT,
            detail=(
                "Too many sign-in attempts for this account. "
                "Please wait a few minutes and try again."
            ),
        )
