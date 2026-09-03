"""add ingredients_hash to allergen_inferences

Cached allergen verdicts were never invalidated, so a product whose ingredient
list changed would keep serving a verdict reached from the old label. Each row
now records a fingerprint of the ingredients and declared allergens it was
based on, and the cache lookup matches on it.

Existing rows are dropped rather than backfilled: there is no way to know what
label they were computed from, and this is a cache that rebuilds itself on
demand.

Revision ID: e2a47f6b19c5
Revises: d5b91c7a3e08
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e2a47f6b19c5'
down_revision: Union[str, None] = 'd5b91c7a3e08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Verdicts of unknown provenance cannot be trusted against the new hash.
    op.execute("DELETE FROM allergen_inferences")
    op.add_column(
        'allergen_inferences',
        sa.Column('ingredients_hash', sa.String(length=64), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('allergen_inferences', 'ingredients_hash')
