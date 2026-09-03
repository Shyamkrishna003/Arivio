"""normalize user_goals.goal_type

Goal types were previously stored verbatim from user input, so rows like
"Weight Loss" or "muscle_building" never matched the canonical GOAL_PROFILES
keys or the normalized CustomGoalProfile rows — those goals silently scored
neutral. The engine now normalizes on write; this backfills existing rows.

Mirrors app.personalization.engine._normalize:
    text.strip().lower().replace("-", " ").replace("_", " ")

Revision ID: a1f4c9b27e10
Revises: 0d6c49207161
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1f4c9b27e10'
down_revision: Union[str, None] = '0d6c49207161'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NORMALIZED = "lower(replace(replace(btrim(goal_type), '-', ' '), '_', ' '))"


def upgrade() -> None:
    # 1. Normalize every stored goal type.
    op.execute(f"UPDATE user_goals SET goal_type = {_NORMALIZED}")

    # 2. Normalization can collapse distinct rows into duplicates for the same
    #    user (e.g. "Weight Loss" and "weight_loss"). A duplicate would be
    #    counted twice in the weighted goal average, so keep the earliest row.
    op.execute(
        """
        DELETE FROM user_goals
        WHERE id NOT IN (
            SELECT MIN(id) FROM user_goals GROUP BY user_id, goal_type
        )
        """
    )

    # 3. Same normalization for the AI-generated custom goal profiles, which are
    #    looked up by the normalized key. goal_type is UNIQUE there, so drop any
    #    row that would collide *before* updating, or the UPDATE violates it.
    op.execute(
        f"""
        DELETE FROM custom_goal_profiles
        WHERE id NOT IN (
            SELECT MIN(id) FROM custom_goal_profiles GROUP BY {_NORMALIZED}
        )
        """
    )
    op.execute(f"UPDATE custom_goal_profiles SET goal_type = {_NORMALIZED}")


def downgrade() -> None:
    # Normalization is lossy — the original casing and separators are not
    # recoverable, and the deduplicated rows are gone. Nothing to undo.
    pass
