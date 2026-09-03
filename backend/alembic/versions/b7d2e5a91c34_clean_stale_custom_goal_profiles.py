"""clean stale custom_goal_profiles

Two data problems predating the goal-resolution fixes:

1. Rows shadowing hardcoded profiles. The engine consults custom_goal_profiles
   before GOAL_PROFILES, so an AI-generated row for a goal we already ship
   silently overrides the curated profile. `users.tasks` now refuses to create
   these, so any that exist are stale artifacts. Observed: "weight loss" and
   "muscle growth" (which resolves to "muscle gain" via GOAL_ALIASES).

2. Thresholds with no direction. A nutrient listed in `thresholds` but in
   neither `prefer_low` nor `prefer_high` cannot be scored — the engine skips
   it, so the AI's intent for that nutrient is lost. `users.tasks` now infers
   direction at write time; this backfills existing rows the same way, from the
   good/bad ordering.

The hardcoded goal keys are frozen as a literal below rather than imported from
app code, so this migration stays deterministic if GOAL_PROFILES/GOAL_ALIASES
change later.

Revision ID: b7d2e5a91c34
Revises: a1f4c9b27e10
Create Date: 2026-09-03

"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b7d2e5a91c34'
down_revision: Union[str, None] = 'a1f4c9b27e10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Snapshot of GOAL_PROFILES keys plus every GOAL_ALIASES key resolving into
# them, as of this revision.
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


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Drop rows that shadow a hardcoded profile ──
    conn.execute(
        sa.text("DELETE FROM custom_goal_profiles WHERE goal_type = ANY(:keys)"),
        {"keys": HARDCODED_GOAL_KEYS},
    )

    # ── 2. Backfill missing threshold directions ──
    rows = conn.execute(sa.text(
        "SELECT id, prefer_low, prefer_high, thresholds FROM custom_goal_profiles"
    )).mappings().all()

    for row in rows:
        thresholds = row["thresholds"] or {}
        prefer_low = list(row["prefer_low"] or [])
        prefer_high = list(row["prefer_high"] or [])

        changed = False
        for key, vals in thresholds.items():
            if key in prefer_low or key in prefer_high:
                continue
            if not isinstance(vals, dict) or "good" not in vals or "bad" not in vals:
                continue
            # Mirrors users.tasks: good < bad means lower is better.
            if vals["good"] < vals["bad"]:
                prefer_low.append(key)
            else:
                prefer_high.append(key)
            changed = True

        # Drop direction entries with no matching threshold — they can never be
        # scored and only mislead anything reading the profile.
        pruned_low = [k for k in prefer_low if k in thresholds]
        pruned_high = [k for k in prefer_high if k in thresholds]
        if pruned_low != prefer_low or pruned_high != prefer_high:
            prefer_low, prefer_high = pruned_low, pruned_high
            changed = True

        if changed:
            conn.execute(
                sa.text(
                    "UPDATE custom_goal_profiles "
                    "SET prefer_low = CAST(:low AS json), "
                    "    prefer_high = CAST(:high AS json) "
                    "WHERE id = :id"
                ),
                {
                    "low": json.dumps(prefer_low),
                    "high": json.dumps(prefer_high),
                    "id": row["id"],
                },
            )


def downgrade() -> None:
    # The deleted rows were regenerable AI output, not authored data, and the
    # inferred directions cannot be distinguished from originals. Nothing to undo.
    pass
