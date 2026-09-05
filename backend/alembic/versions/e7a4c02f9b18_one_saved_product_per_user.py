"""one saved-product row per user and product

Saving is a toggle, not a log: a user has either saved a product or not. The
table never said so, and until now nothing wrote to it, so no duplicates could
accumulate — this lands with the save endpoint that first can.

The constraint is not just hygiene. It is what lets POST /products/{id}/save be
a single ON CONFLICT upsert, which is the only thing that closes the race
between two concurrent saves: a double-clicked button issues two requests, both
see no row, and an application-level check cannot settle that whatever order it
runs in. The same race is what put products in Recent Activity twice, fixed the
same way in c8e1d4a70f52.

The dedupe below is defensive. Keeping MIN(id) keeps the earliest save, which
matches what the row means — when the user first saved this product.

Revision ID: e7a4c02f9b18
Revises: c8e1d4a70f52
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7a4c02f9b18'
down_revision: Union[str, None] = 'c8e1d4a70f52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM saved_products
        WHERE id NOT IN (
            SELECT MIN(id) FROM saved_products GROUP BY user_id, product_id
        )
        """
    )
    op.create_unique_constraint(
        'uq_saved_product_user_product',
        'saved_products',
        ['user_id', 'product_id'],
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_saved_product_user_product', 'saved_products', type_='unique'
    )
