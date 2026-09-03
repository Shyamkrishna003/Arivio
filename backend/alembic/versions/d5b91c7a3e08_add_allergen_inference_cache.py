"""add allergen_inferences cache table

Allergens outside the engine's synonym tables can only be matched by literal
name, which misses spelling variants, translations, E-numbers and derived
ingredients. We fall back to asking an LLM to read the ingredient list, and
cache the verdict per (allergen, product) so a pair costs one call ever.

Revision ID: d5b91c7a3e08
Revises: c3e8f1a05d72
Create Date: 2026-09-04

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd5b91c7a3e08'
down_revision: Union[str, None] = 'c3e8f1a05d72'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'allergen_inferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('allergen', sa.String(length=100), nullable=False),
        sa.Column('product_id', sa.Integer(), nullable=False),
        sa.Column('found', sa.Boolean(), nullable=False),
        sa.Column('certainty', sa.String(length=20), nullable=False, server_default='medium'),
        sa.Column('matched_ingredient', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('allergen', 'product_id', name='uq_allergen_inference'),
    )
    op.create_index(op.f('ix_allergen_inferences_id'), 'allergen_inferences', ['id'])
    op.create_index(
        'ix_allergen_inference_lookup', 'allergen_inferences', ['product_id', 'allergen']
    )


def downgrade() -> None:
    op.drop_index('ix_allergen_inference_lookup', table_name='allergen_inferences')
    op.drop_index(op.f('ix_allergen_inferences_id'), table_name='allergen_inferences')
    op.drop_table('allergen_inferences')
