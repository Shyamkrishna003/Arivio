"""enforce uniqueness for barcodes, report feedback and active goals

Three places relied on there being at most one row, without the schema saying
so. Each had an application path that could create a second row, and a read
that raised (rather than coping) once one existed:

  - product_identifiers.identifier_value — the barcode lookup keys on the value
    alone. A second row for the same barcode made that lookup ambiguous and
    every later scan of it failed. Two routes could create one: submitting a
    product with a barcode already on file, and two concurrent scans of an
    unknown barcode both importing it from Open Food Facts. Only a constraint
    closes the race; the application check cannot.

  - report_feedback (user_id, product_id) — one rating per user per product.
    Submitting again now updates the existing row, but nothing stopped earlier
    inserts from stacking up, and the read expects a single row.

  - user_goals (user_id, goal_type) — a duplicate goal is counted twice in the
    weighted goal average, quietly inflating or deflating every score. Scoped
    to ACTIVE goals with a partial index, mirroring the application guard,
    which also only considers active goals.

The application guard in add_goal is still the primary defence for goals and
does strictly more than this index: it resolves aliases, so "weight loss" and
"weight management" are rejected as the same goal even though the stored text
differs. The index catches only exact repeats of the stored text. They are
complementary, not redundant.

Existing duplicates are collapsed before each constraint is added, or it would
fail to apply. Which row survives matches what the corresponding read now
returns: earliest for barcodes and goals, most recent for feedback.

Revision ID: c9e1a7f3d520
Revises: f8c3d240ab71
Create Date: 2026-09-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c9e1a7f3d520'
down_revision: Union[str, None] = 'f8c3d240ab71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Barcodes ──
    # Keep the earliest row, matching the lookup's ORDER BY id LIMIT 1.
    op.execute(
        """
        DELETE FROM product_identifiers
        WHERE id NOT IN (
            SELECT MIN(id) FROM product_identifiers GROUP BY identifier_value
        )
        """
    )
    op.create_unique_constraint(
        'uq_product_identifier_value', 'product_identifiers', ['identifier_value'],
    )

    # ── Report feedback ──
    # Keep the most recent rating, matching the read's ORDER BY created_at DESC
    # and the submit path, which now updates that same row.
    op.execute(
        """
        DELETE FROM report_feedback
        WHERE id NOT IN (
            SELECT MAX(id) FROM report_feedback GROUP BY user_id, product_id
        )
        """
    )
    op.create_unique_constraint(
        'uq_report_feedback_user_product', 'report_feedback', ['user_id', 'product_id'],
    )

    # ── Active goals ──
    # Partial index: a goal the user has removed must not block re-adding it.
    op.execute(
        """
        DELETE FROM user_goals
        WHERE is_active
          AND id NOT IN (
            SELECT MIN(id) FROM user_goals WHERE is_active GROUP BY user_id, goal_type
        )
        """
    )
    op.create_index(
        'uq_user_goal_active',
        'user_goals',
        ['user_id', 'goal_type'],
        unique=True,
        postgresql_where=sa.text('is_active'),
    )


def downgrade() -> None:
    # The collapsed duplicate rows are gone and are not recoverable; only the
    # constraints come off.
    op.drop_index('uq_user_goal_active', table_name='user_goals')
    op.drop_constraint('uq_report_feedback_user_product', 'report_feedback', type_='unique')
    op.drop_constraint('uq_product_identifier_value', 'product_identifiers', type_='unique')
