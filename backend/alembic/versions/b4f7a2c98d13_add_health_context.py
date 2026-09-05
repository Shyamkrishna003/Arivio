"""health documents and health-context consent

Adds the storage for the health-context feature: markers read out of a user's
own lab reports, and the explicit consent that must precede processing them
(PRD §10).

Two things this table deliberately does NOT have:

  - **A column for the document.** No path, no blob. Uploaded reports are
    parsed in memory and discarded; only the numbers the user confirms are
    kept. That removes the retention policy, the encrypted blob store, and any
    chance of a lab report ending up under the public /uploads mount that
    serves product photos.

  - **Queryable results.** Markers live in one Fernet-encrypted JSON blob, so
    the analyte and its high/low flag are as opaque as the value. Encrypting
    only the number would leave `analyte='hba1c', flag='high'` readable, which
    is the sensitive part. The schema therefore cannot answer "which users
    have elevated glucose" — which is the point.

`privacy_settings.health_context_consent_at` records consent with a timestamp
rather than a boolean, so withdrawal is a null and "when did they agree" is
answerable. There is deliberately no `share_health_context` companion to the
other share_* flags: PRD §10 says health context must never be exposed through
community profiles, and not offering the switch is how that is enforced.

Revision ID: b4f7a2c98d13
Revises: a7c31f9d5b60
Create Date: 2026-09-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b4f7a2c98d13'
down_revision: Union[str, None] = 'a7c31f9d5b60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'health_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('document_type', sa.String(length=50), nullable=False,
                  server_default='blood_test'),
        # Fernet token. Text, not JSON: the database must not be able to read
        # into it, and a JSON column would invite exactly that.
        sa.Column('markers_encrypted', sa.Text(), nullable=False),
        sa.Column('marker_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('extraction_source', sa.String(length=30), nullable=False,
                  server_default='manual'),
        sa.Column('extraction_model', sa.String(length=120), nullable=True),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        # Deleting the account takes the health data with it. "Delete means
        # delete" is the promise the consent screen makes.
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_health_documents_id', 'health_documents', ['id'])
    op.create_index(
        'ix_health_documents_user', 'health_documents', ['user_id', 'uploaded_at']
    )

    op.add_column(
        'privacy_settings',
        sa.Column('health_context_consent_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('privacy_settings', 'health_context_consent_at')
    op.drop_index('ix_health_documents_user', table_name='health_documents')
    op.drop_index('ix_health_documents_id', table_name='health_documents')
    op.drop_table('health_documents')
