"""add prompt_version and put model in the allergen cache key

A cached verdict depends on the model that produced it and the prompt it was
asked with, not just the ingredient list. Previously a model swap or a prompt
edit kept serving verdicts reached under the old configuration.

The unique constraint widens to (allergen, product_id, model, prompt_version)
so verdicts from different models coexist — switching AI_MODEL and back does
not force a re-run. ingredients_hash stays out of the key: a changed label
should REPLACE the stale verdict, not accumulate beside it.

Existing rows are dropped: they predate prompt_version and cannot be attributed
to a revision. This is a cache and rebuilds on demand.

Revision ID: f8c3d240ab71
Revises: e2a47f6b19c5
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f8c3d240ab71'
down_revision: Union[str, None] = 'e2a47f6b19c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM allergen_inferences")

    op.add_column(
        'allergen_inferences',
        sa.Column('prompt_version', sa.Integer(), nullable=False, server_default='1'),
    )
    # model joins the key, so it can no longer be NULL — Postgres treats NULLs
    # as distinct and uniqueness would not hold.
    op.alter_column(
        'allergen_inferences', 'model',
        existing_type=sa.String(length=100), nullable=False,
    )

    op.drop_constraint('uq_allergen_inference', 'allergen_inferences', type_='unique')
    op.create_unique_constraint(
        'uq_allergen_inference', 'allergen_inferences',
        ['allergen', 'product_id', 'model', 'prompt_version'],
    )

    op.alter_column('allergen_inferences', 'prompt_version', server_default=None)


def downgrade() -> None:
    op.execute("DELETE FROM allergen_inferences")
    op.drop_constraint('uq_allergen_inference', 'allergen_inferences', type_='unique')
    op.create_unique_constraint(
        'uq_allergen_inference', 'allergen_inferences', ['allergen', 'product_id'],
    )
    op.alter_column(
        'allergen_inferences', 'model',
        existing_type=sa.String(length=100), nullable=True,
    )
    op.drop_column('allergen_inferences', 'prompt_version')
