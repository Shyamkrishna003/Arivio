"""add profile_status and status_message to user_goals

Goals are free text, so a user can type something that is not a scoreable
nutritional goal ("no muscle", "sleep better"). The AI profile generator now
acts as a sanity gate and may decline, so each goal needs to carry whether it
can be scored and a user-facing reason when it cannot.

Backfill: every existing goal that resolves to a hardcoded profile or already
has a CustomGoalProfile row is marked "ready"; anything else is "unsupported",
since its profile was never generated and it has been silently contributing a
neutral 50.

Revision ID: c3e8f1a05d72
Revises: b7d2e5a91c34
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c3e8f1a05d72'
down_revision: Union[str, None] = 'b7d2e5a91c34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Snapshot of GOAL_PROFILES keys plus resolving GOAL_ALIASES keys, frozen so
# this migration stays deterministic if the app's goal tables change later.
HARDCODED_GOAL_KEYS = [
    'athletic performance', 'balanced diet', 'blood pressure management',
    'blood sugar control', 'blood sugar management', 'build muscle', 'bulking',
    'cardiac health', 'cardiovascular health', 'cholesterol management',
    'cutting', 'diabetes', 'diabetes management', 'endurance', 'energy',
    'energy boost', 'fat loss', 'gain muscle', 'general health',
    'general wellness', 'glucose management', 'healthy eating', 'heart health',
    'lose weight', 'lower cholesterol', 'more energy', 'muscle building',
    'muscle gain', 'muscle growth', 'overall health', 'prediabetes',
    'slimming', 'strength building', 'weight control', 'weight loss',
    'weight management', 'wellbeing',
]

_UNSUPPORTED_MSG = (
    "We couldn't build a scoring profile for this goal, so it isn't affecting "
    "your scores. Try removing and re-adding it."
)


def upgrade() -> None:
    op.add_column(
        'user_goals',
        sa.Column('profile_status', sa.String(length=20), nullable=False,
                  server_default='pending'),
    )
    op.add_column(
        'user_goals',
        sa.Column('status_message', sa.String(length=500), nullable=True),
    )

    # Ready: resolves to a hardcoded profile...
    op.execute(
        sa.text(
            "UPDATE user_goals SET profile_status = 'ready', status_message = NULL "
            "WHERE goal_type = ANY(:keys)"
        ).bindparams(keys=HARDCODED_GOAL_KEYS)
    )
    # ...or already has a generated custom profile.
    op.execute(
        """
        UPDATE user_goals SET profile_status = 'ready', status_message = NULL
        WHERE goal_type IN (SELECT goal_type FROM custom_goal_profiles)
        """
    )
    # Everything still pending never got a profile and never will without a
    # retry — surface that rather than leaving it silently unscored.
    op.execute(
        sa.text(
            "UPDATE user_goals SET profile_status = 'unsupported', status_message = :msg "
            "WHERE profile_status = 'pending'"
        ).bindparams(msg=_UNSUPPORTED_MSG)
    )

    # The server default was only needed to backfill existing rows; the app
    # sets the value explicitly on insert.
    op.alter_column('user_goals', 'profile_status', server_default=None)


def downgrade() -> None:
    op.drop_column('user_goals', 'status_message')
    op.drop_column('user_goals', 'profile_status')
