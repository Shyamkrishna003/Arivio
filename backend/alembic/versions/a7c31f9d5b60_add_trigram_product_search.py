"""trigram indexes for fuzzy product search, and an external-id dedupe guard

Product search was `name ILIKE '%q%' OR brand ILIKE '%q%'`. A leading wildcard
cannot use a btree index, so every search sequentially scanned the whole
products table, and it only ever matched literal substrings — "amul buter" or
"maggie" found nothing.

pg_trgm fixes both halves at once. A GIN index over trigrams accelerates
`ILIKE '%...%'`, the similarity operator `%`, and the word-similarity operator
`<%`, so the same index serves exact-substring, whole-string-fuzzy and
"typed text matches part of the name" lookups.

Two indexes are created:

  - ix_products_search_trgm over `coalesce(brand,'') || ' ' || name`. This is
    the one search actually ranks on: a query like "amul butter" scores poorly
    against the name ("Butter") and against the brand ("Amul") taken
    separately, but well against the two joined. Both `||` and `coalesce` are
    immutable, so the expression is indexable.

  - ix_products_name_trgm over `name` alone, for matching where the brand is
    not part of the query — label OCR, and alternative-product matching.

pg_trgm folds trigrams to lower case, so neither index needs a lower() wrapper
and both are case-insensitive.

unaccent is deliberately not applied: it is not immutable without a wrapper
function, and accented product names are rare in this catalogue. Add it later
as a custom IMMUTABLE wrapper if it proves necessary.

The second change is a uniqueness guard. Products imported from Open Food
Facts by barcode are deduplicated by product_identifiers.identifier_value, but
products imported from a *name* search may arrive with no barcode at all —
nothing then stopped the same OFF product being imported again on every
selection. A partial unique index on (external_source, external_id) closes
that; partial because both columns are null for user-submitted products, and
because "user:<id>" external sources have no external_id.

Note this migration only adds indexes and clears a redundant column value, so
it holds no long locks and destroys no rows.

Revision ID: a7c31f9d5b60
Revises: d3b6f81c4a92
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c31f9d5b60'
down_revision: Union[str, None] = 'd3b6f81c4a92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Trigram search ──
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.execute(
        """
        CREATE INDEX ix_products_search_trgm
        ON products
        USING gin ((coalesce(brand, '') || ' ' || name) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_products_name_trgm
        ON products
        USING gin (name gin_trgm_ops)
        """
    )

    # ── External-source dedupe ──
    # Any pre-existing duplicate has to stop colliding before the index can be
    # created. The later copies have their external_id cleared rather than
    # being deleted: every table referencing products cascades on delete, so
    # dropping a duplicate would take its community reviews, scan history and
    # saved-product entries with it — real user data, to enforce a constraint
    # that only governs re-import. Clearing the reference is enough, because
    # the index is partial on external_id IS NOT NULL.
    #
    # The earliest row keeps its reference: it is the one product_identifiers
    # already points at, so it is what every barcode lookup resolves to.
    op.execute(
        """
        UPDATE products p
        SET external_id = NULL
        WHERE p.external_source IS NOT NULL
          AND p.external_id IS NOT NULL
          AND p.id > (
              SELECT MIN(q.id) FROM products q
              WHERE q.external_source = p.external_source
                AND q.external_id = p.external_id
          )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_products_external_ref
        ON products (external_source, external_id)
        WHERE external_source IS NOT NULL AND external_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_products_external_ref")
    op.execute("DROP INDEX IF EXISTS ix_products_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_products_search_trgm")
    # pg_trgm is left installed: other objects may depend on it, and dropping
    # an extension is not something a schema downgrade should decide.
