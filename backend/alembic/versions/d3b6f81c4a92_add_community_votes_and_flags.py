"""add review votes and flags, one review per user per product

community_reviews and review_contexts already existed but carried no way to
record who voted or who reported a review — only bare counters on the review
itself, which can be incremented without limit. That is the vote-stuffing the
manipulation-prevention requirement asks us to prevent, so votes and reports
each get a row keyed to their author.

community_reviews also gains a uniqueness guard: one experience per user per
product. A repeated voice would skew the aggregate percentages the product page
reports, and duplicate submissions are the simplest manipulation there is.
Existing duplicates are collapsed to the most recent, which is the one the
submit path now updates.

Revision ID: d3b6f81c4a92
Revises: c9e1a7f3d520
Create Date: 2026-09-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd3b6f81c4a92'
down_revision: Union[str, None] = 'c9e1a7f3d520'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'review_votes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('review_id', sa.Integer(), nullable=False),
        sa.Column('is_helpful', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['review_id'], ['community_reviews.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'review_id', name='uq_review_vote_user_review'),
    )
    op.create_index(op.f('ix_review_votes_id'), 'review_votes', ['id'])
    # The vote counts are recomputed per review on every vote.
    op.create_index('ix_review_votes_review_id', 'review_votes', ['review_id'])

    op.create_table(
        'review_flags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('review_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['review_id'], ['community_reviews.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'review_id', name='uq_review_flag_user_review'),
    )
    op.create_index(op.f('ix_review_flags_id'), 'review_flags', ['id'])
    op.create_index('ix_review_flags_review_id', 'review_flags', ['review_id'])

    # Keep the most recent experience per (user, product) — the submit path
    # updates that row rather than inserting beside it.
    op.execute(
        """
        DELETE FROM community_reviews
        WHERE id NOT IN (
            SELECT MAX(id) FROM community_reviews GROUP BY user_id, product_id
        )
        """
    )
    op.create_unique_constraint(
        'uq_community_review_user_product', 'community_reviews', ['user_id', 'product_id'],
    )

    # The product page always filters to published reviews for one product.
    op.create_index(
        'ix_community_reviews_product_approved',
        'community_reviews', ['product_id', 'is_approved'],
    )


def downgrade() -> None:
    op.drop_index('ix_community_reviews_product_approved', table_name='community_reviews')
    op.drop_constraint('uq_community_review_user_product', 'community_reviews', type_='unique')
    op.drop_index('ix_review_flags_review_id', table_name='review_flags')
    op.drop_index(op.f('ix_review_flags_id'), table_name='review_flags')
    op.drop_table('review_flags')
    op.drop_index('ix_review_votes_review_id', table_name='review_votes')
    op.drop_index(op.f('ix_review_votes_id'), table_name='review_votes')
    op.drop_table('review_votes')
