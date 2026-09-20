"""
Database URL normalisation for managed Postgres.

Hosted providers hand out a libpq-shaped URL — `postgresql://`, or on some
dashboards still `postgres://`, with `sslmode=require` in the query string.
asyncpg accepts neither: the scheme has to name the driver, and libpq's TLS
parameters reach it as unknown keyword arguments, which raise rather than being
ignored. So pasting a Neon or Supabase URL straight into DATABASE_URL fails at
connect time, with a TypeError that mentions nothing about TLS.

Rewriting it here rather than at each call site keeps the application engine
and Alembic on identical connection settings. That matters because migrations
run on the same async driver: a URL that fails for one fails for the other, and
discovering that during a deploy — after the image has built, from a log — is
considerably worse than discovering it at startup.

A local URL with no TLS parameters passes through untouched, so docker-compose
and a plain `uvicorn` run are unaffected.
"""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Understood by libpq, rejected by asyncpg. Lifted out of the query string and
# expressed as the `ssl` connect argument instead.
_LIBPQ_TLS_PARAMS = frozenset({
    "sslmode",
    "channel_binding",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "gssencmode",
})

# Schemes that name no driver, and so default to psycopg2 under SQLAlchemy.
_DRIVERLESS_SCHEMES = frozenset({"postgres", "postgresql"})

_ASYNCPG_SCHEME = "postgresql+asyncpg"


def normalize_database_url(
    url: str,
    *,
    prepared_statements: bool = True,
) -> tuple[str, dict[str, Any]]:
    """
    Return (url, connect_args) ready to hand to create_async_engine.

    Names asyncpg in the scheme, lifts libpq's TLS parameters out of the query
    string, and converts them into the `ssl` argument asyncpg expects. With
    `prepared_statements=False` it also disables both statement caches, which
    is what a transaction-mode connection pooler requires — see
    DATABASE_PREPARED_STATEMENTS in config.py for when that applies.
    """
    parts = urlsplit(url)

    scheme = parts.scheme
    if scheme in _DRIVERLESS_SCHEMES:
        scheme = _ASYNCPG_SCHEME
    is_asyncpg = scheme.endswith("+asyncpg")

    # Parameters we do not recognise are left alone: some are meaningful to
    # asyncpg or to the SQLAlchemy dialect, and dropping one silently would be
    # a worse failure than passing it through.
    kept: list[tuple[str, str]] = []
    tls: dict[str, str] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _LIBPQ_TLS_PARAMS:
            tls[key] = value
        else:
            kept.append((key, value))

    connect_args: dict[str, Any] = {}

    if is_asyncpg:
        sslmode = tls.get("sslmode")
        if sslmode is not None:
            connect_args["ssl"] = _ssl_argument(sslmode)

        if not prepared_statements:
            # Two caches, two owners. asyncpg's own is switched off through a
            # connect argument; the SQLAlchemy dialect keeps a separate one
            # that is only configurable through the URL.
            connect_args["statement_cache_size"] = 0
            kept = [(k, v) for k, v in kept if k != "prepared_statement_cache_size"]
            kept.append(("prepared_statement_cache_size", "0"))

    normalized = urlunsplit((
        scheme,
        # Verbatim, so a password containing reserved characters survives.
        parts.netloc,
        parts.path,
        urlencode(kept),
        parts.fragment,
    ))
    return normalized, connect_args


def _ssl_argument(sslmode: str) -> Any:
    """
    Translate an sslmode value into asyncpg's `ssl` argument.

    Note that this is deliberately stricter than libpq for `require`, which
    there means "encrypt, but do not check who answered". Every provider we
    target presents a certificate from a public CA, so verifying costs nothing
    and closes the machine-in-the-middle that bare encryption leaves open.
    """
    mode = sslmode.strip().lower()

    if mode == "disable":
        return False

    context = ssl.create_default_context()

    if mode == "no-verify":
        # Not libpq vocabulary — our own escape hatch, for a provider whose
        # certificate is self-signed or issued by a private CA. It encrypts but
        # cannot detect an interposed host, so reach for it only once a
        # verifying connection has actually been shown to fail.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    return context
