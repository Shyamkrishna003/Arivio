"""
Exercise fuzzy product search against the real database.

Run after `alembic upgrade head`, from the backend directory:

    python test/test_search.py

Checks, in order:
  1. pg_trgm is installed and the trigram indexes exist.
  2. The candidate filter is actually served by an index, not a sequential
     scan — the whole point of the migration. This is asserted from EXPLAIN
     rather than assumed, because the index only applies when the query's
     expression matches the indexed one character for character, and nothing
     warns you when it silently stops matching.
  3. Fuzzy queries return the products a user meant: partial names,
     misspellings, brand+name in either order.

Seeds its own products under a marker brand and removes them afterwards, so it
is safe to run against a database with real data in it.
"""

import asyncio
import os
import sys

# Running a script puts its own directory on sys.path, not the project root,
# so `app` is not importable without this. Same bootstrap as alembic/env.py.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import delete, text  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402

# Every model module, so the mapper registry is complete before the first
# query. Importing only the ones this script names is not enough — the
# relationships between them are resolved by class name at mapper
# configuration time, and a missing module fails the whole registry. Same list
# as alembic/env.py.
import app.users.models  # noqa: F401,E402
import app.products.models  # noqa: F401,E402
import app.ingredients.models  # noqa: F401,E402
import app.community.models  # noqa: F401,E402
import app.ai.models  # noqa: F401,E402
import app.allergens.models  # noqa: F401,E402
import app.health.models  # noqa: F401,E402

from app.products.models import DataQuality, Product, VerificationStatus  # noqa: E402
from app.products.search import search_products, suggest_products  # noqa: E402

MARKER = "ARIVIO_SEARCH_TEST"

SEED = [
    ("Pasteurised Butter 500g", "Amul", "Dairy"),
    ("Instant Noodles Masala", "Maggi", "Snacks"),
    ("Dark Chocolate 70% Cocoa", "Lindt", "Confectionery"),
    ("Greek Yoghurt Natural", "Epigamia", "Dairy"),
]

# (query, substring that must appear in the top result's name or brand)
CASES = [
    ("butter", "Butter"),          # partial name
    ("amul butter", "Amul"),       # brand + name, joined
    ("butter amul", "Amul"),       # same, reversed
    ("maggie", "Maggi"),           # misspelling
    ("noodels", "Noodles"),        # transposed letters
    ("choclate", "Chocolate"),     # dropped letter
    ("yoghurt", "Yoghurt"),        # exact word, mid-name
]

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  {'✅' if condition else '❌'} {message}")
    if not condition:
        failures.append(message)


async def seed(db) -> None:
    for name, brand, category in SEED:
        db.add(Product(
            name=name,
            brand=brand,
            category=category,
            description=MARKER,
            verification_status=VerificationStatus.UNVERIFIED,
            data_quality=DataQuality.MEDIUM,
        ))
    await db.commit()


async def cleanup(db) -> None:
    await db.execute(delete(Product).where(Product.description == MARKER))
    await db.commit()


async def main() -> int:
    async with AsyncSessionLocal() as db:
        print("\n── Extension and indexes ──")
        has_trgm = (await db.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'")
        )).scalar_one()
        check(bool(has_trgm), "pg_trgm extension installed")
        if not has_trgm:
            print("\n  Run `alembic upgrade head` first.\n")
            return 1

        indexes = set((await db.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'products'"
        ))).scalars().all())
        check("ix_products_search_trgm" in indexes, "brand+name trigram index exists")
        check("ix_products_name_trgm" in indexes, "name trigram index exists")
        check("uq_products_external_ref" in indexes, "external-reference dedupe index exists")

        await cleanup(db)
        await seed(db)

        try:
            print("\n── Index is actually used ──")
            # A sequential scan here means the query expression drifted from
            # the indexed one and every search silently got slower.
            await db.execute(text(
                "SELECT set_config('pg_trgm.word_similarity_threshold', '0.32', true)"
            ))
            plan = "\n".join((await db.execute(text(
                """
                EXPLAIN
                SELECT id FROM products
                WHERE 'amul butter' <% (coalesce(brand, '') || ' ' || name)
                """
            ))).scalars().all())
            # The planner legitimately prefers a sequential scan on a tiny
            # table, so force its hand to prove the index is usable at all.
            await db.execute(text("SET LOCAL enable_seqscan = off"))
            forced_plan = "\n".join((await db.execute(text(
                """
                EXPLAIN
                SELECT id FROM products
                WHERE 'amul butter' <% (coalesce(brand, '') || ' ' || name)
                """
            ))).scalars().all())
            check(
                "ix_products_search_trgm" in forced_plan,
                "trigram index is usable for the candidate filter",
            )
            if "ix_products_search_trgm" not in forced_plan:
                print(f"\n  Plan was:\n{forced_plan}\n  (unforced:\n{plan})")
            await db.execute(text("SET LOCAL enable_seqscan = on"))

            print("\n── Fuzzy matching ──")
            for query, expected in CASES:
                matches, total = await search_products(db, query, limit=5)
                top = matches[0].product if matches else None
                haystack = f"{top.brand or ''} {top.name}" if top else ""
                ok = expected.lower() in haystack.lower()
                detail = (
                    f'"{query}" → {haystack.strip()} ({matches[0].score:.2f}, {total} hits)'
                    if top else f'"{query}" → no results'
                )
                check(ok, detail)

            print("\n── Typeahead ──")
            suggestions = await suggest_products(db, "amu", limit=8)
            check(
                any("Amul" in (s.product.brand or "") for s in suggestions),
                f'"amu" suggests the Amul product ({len(suggestions)} suggestions)',
            )
            check(
                all(
                    suggestions[i].score >= suggestions[i + 1].score
                    for i in range(len(suggestions) - 1)
                ),
                "suggestions are ordered best-match first",
            )

            print("\n── Non-matches ──")
            matches, _ = await search_products(db, "xyzzyqwerty", limit=5)
            check(not matches, "an unrelated query returns nothing")

            # Trigram scores are meaningless for a single character, and the
            # dropdown would be noise — the frontend requires 2 characters.
            matches, _ = await search_products(db, "   ", limit=5)
            check(not matches, "a whitespace-only query returns nothing")
        finally:
            await cleanup(db)

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ All search checks passed.")
    return 0


sys.exit(asyncio.run(main()))
