"""one scan-history row per user and product

Recent Activity showed the same product twice after a scan.

user_scan_history records "the last time this user looked at this product", so
a (user, product) pair can only appear once — but nothing said so. add_to_history
selected the row and inserted it if absent, and two concurrent calls both saw
it absent and both inserted. React StrictMode double-invokes the effect that
records a view, so in development that race ran on essentially every scan.

An application-level check cannot close it whatever order it runs in; only the
database can. The endpoint is now a single ON CONFLICT upsert against this
constraint.

test/remove_history_duplicates.py existed to clear these by hand. This does the
same cleanup once, and the constraint stops them coming back, so that script is
now redundant.

Keeping MAX(id) matches both the old cleanup script and the read it feeds:
Recent Activity orders by scanned_at descending, and the later row carries the
more recent timestamp.

Revision ID: c8e1d4a70f52
Revises: b4f7a2c98d13
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8e1d4a70f52'
down_revision: Union[str, None] = 'b4f7a2c98d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM user_scan_history
        WHERE id NOT IN (
            SELECT MAX(id) FROM user_scan_history GROUP BY user_id, product_id
        )
        """
    )
    op.create_unique_constraint(
        'uq_scan_history_user_product',
        'user_scan_history',
        ['user_id', 'product_id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_scan_history_user_product', 'user_scan_history', type_='unique'
    )
