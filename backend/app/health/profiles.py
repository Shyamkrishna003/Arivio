"""
Health markers → nutritional guidance.

This is the deliberate centre of the health feature, and the reason a language
model does not compute any part of the score.

The rest of this engine is explainable by construction: GOAL_PROFILES is a
table of `prefer_low` / `prefer_high` / `thresholds`, and every finding is a
flag with a stated impact. Health context enters the same way — as a table,
evaluated by the same arithmetic. A model reads the numbers off a document;
from there on, everything is deterministic, reproducible, and testable, and it
can be shown to the user as a rule rather than asserted as a verdict.

Two consequences worth stating:

  - The same markers and the same product always produce the same score. A
    medical adjustment that drifted between page loads would be indefensible.
  - Every condition below is a *pattern of readings*, never a diagnosis. The
    labels say "elevated blood sugar", not "diabetes", and the user-facing
    text is written so it stays true even when the pattern has some other
    cause. PRD §10: the platform must not diagnose.

Nutrient keys match the dict the personalization router builds, so a profile
here scores against exactly the fields the engine already sees.
"""

from typing import Optional

# ──────────────────────────────────────────────
# Conditions
# ──────────────────────────────────────────────
#
# `triggers`: any one matching activates the condition. `flag` is what
# analytes.flag_for() produced, so the report's own reference range decides
# where present.
#
# `prefer_low` / `prefer_high` / `thresholds` / `weights` mirror GOAL_PROFILES
# exactly, so the engine scores them with the same code path.
#
# `severity` scales how far the condition can move a product's score:
#   "high"     — the reading is well outside range and the nutrient link is
#                well established (kidney function and potassium, say)
#   "moderate" — a clear link worth acting on
#   "low"      — worth mentioning, weak nutritional lever

HEALTH_CONDITION_PROFILES: dict[str, dict] = {
    "elevated_glucose": {
        "label": "Elevated blood sugar",
        "triggers": [
            {"analyte": "hba1c", "flag": "high"},
            {"analyte": "fasting_glucose", "flag": "high"},
            {"analyte": "random_glucose", "flag": "high"},
        ],
        "severity": "high",
        "prefer_low": ["total_sugars_g", "added_sugars_g", "total_carbohydrates_g"],
        "prefer_high": ["fiber_g"],
        "thresholds": {
            "total_sugars_g": {"good": 5, "bad": 22.5},
            "added_sugars_g": {"good": 2, "bad": 15},
            "total_carbohydrates_g": {"good": 20, "bad": 60},
            "fiber_g": {"good": 6, "bad": 1.5},
        },
        "weights": {
            "total_sugars_g": 3.0,
            "added_sugars_g": 2.0,
            "total_carbohydrates_g": 1.5,
            "fiber_g": 1.5,
        },
        "explanation": (
            "Your uploaded results show blood sugar above the usual range. "
            "Foods high in sugar and refined carbohydrate raise blood glucose "
            "fastest; fibre slows that rise."
        ),
    },
    "high_ldl_cholesterol": {
        "label": "Elevated LDL cholesterol",
        "triggers": [
            {"analyte": "ldl_cholesterol", "flag": "high"},
            {"analyte": "total_cholesterol", "flag": "high"},
        ],
        "severity": "high",
        "prefer_low": ["saturated_fat_g", "trans_fat_g", "cholesterol_mg"],
        "prefer_high": ["fiber_g"],
        "thresholds": {
            "saturated_fat_g": {"good": 1.5, "bad": 5},
            "trans_fat_g": {"good": 0.1, "bad": 1},
            "cholesterol_mg": {"good": 20, "bad": 100},
            "fiber_g": {"good": 6, "bad": 1.5},
        },
        "weights": {
            "saturated_fat_g": 3.0,
            "trans_fat_g": 3.0,
            "cholesterol_mg": 1.0,
            "fiber_g": 1.5,
        },
        "explanation": (
            "Your uploaded results show LDL or total cholesterol above the "
            "usual range. Saturated and trans fats raise LDL most; soluble "
            "fibre lowers it."
        ),
    },
    "high_triglycerides": {
        "label": "Elevated triglycerides",
        "triggers": [{"analyte": "triglycerides", "flag": "high"}],
        "severity": "moderate",
        "prefer_low": ["total_sugars_g", "added_sugars_g", "saturated_fat_g"],
        "prefer_high": ["fiber_g"],
        "thresholds": {
            "total_sugars_g": {"good": 5, "bad": 22.5},
            "added_sugars_g": {"good": 2, "bad": 15},
            "saturated_fat_g": {"good": 1.5, "bad": 5},
            "fiber_g": {"good": 6, "bad": 1.5},
        },
        "weights": {
            "total_sugars_g": 2.5,
            "added_sugars_g": 2.5,
            "saturated_fat_g": 1.5,
            "fiber_g": 1.0,
        },
        "explanation": (
            "Your uploaded results show triglycerides above the usual range. "
            "Added sugars raise triglycerides particularly strongly."
        ),
    },
    "low_hdl_cholesterol": {
        "label": "Low HDL cholesterol",
        "triggers": [{"analyte": "hdl_cholesterol", "flag": "low"}],
        "severity": "low",
        "prefer_low": ["trans_fat_g", "added_sugars_g"],
        "prefer_high": [],
        "thresholds": {
            "trans_fat_g": {"good": 0.1, "bad": 1},
            "added_sugars_g": {"good": 2, "bad": 15},
        },
        "weights": {"trans_fat_g": 3.0, "added_sugars_g": 1.0},
        "explanation": (
            "Your uploaded results show HDL below the usual range. Trans fats "
            "lower HDL further."
        ),
    },
    "reduced_kidney_function": {
        "label": "Reduced kidney function",
        "triggers": [
            {"analyte": "egfr", "flag": "low"},
            {"analyte": "creatinine", "flag": "high"},
        ],
        "severity": "high",
        "prefer_low": ["sodium_mg", "potassium_mg", "protein_g"],
        "prefer_high": [],
        "thresholds": {
            "sodium_mg": {"good": 120, "bad": 500},
            "potassium_mg": {"good": 150, "bad": 500},
            "protein_g": {"good": 5, "bad": 20},
        },
        "weights": {"sodium_mg": 3.0, "potassium_mg": 2.5, "protein_g": 2.0},
        "explanation": (
            "Your uploaded results suggest reduced kidney function. Kidneys "
            "clear sodium, potassium and the by-products of protein, so all "
            "three are usually moderated — but the right level is individual "
            "and belongs with your doctor."
        ),
    },
    "elevated_potassium": {
        "label": "Elevated potassium",
        "triggers": [{"analyte": "serum_potassium", "flag": "high"}],
        "severity": "high",
        "prefer_low": ["potassium_mg"],
        "prefer_high": [],
        "thresholds": {"potassium_mg": {"good": 150, "bad": 450}},
        "weights": {"potassium_mg": 3.0},
        "explanation": (
            "Your uploaded results show potassium above the usual range."
        ),
    },
    "anemia_risk": {
        "label": "Low haemoglobin or iron stores",
        "triggers": [
            {"analyte": "hemoglobin", "flag": "low"},
            {"analyte": "ferritin", "flag": "low"},
        ],
        "severity": "moderate",
        "prefer_low": [],
        "prefer_high": ["iron_mg", "vitamin_c_mg"],
        "thresholds": {
            "iron_mg": {"good": 3, "bad": 0.3},
            "vitamin_c_mg": {"good": 15, "bad": 1},
        },
        "weights": {"iron_mg": 3.0, "vitamin_c_mg": 1.0},
        "explanation": (
            "Your uploaded results show haemoglobin or iron stores below the "
            "usual range. Iron-rich foods help, and vitamin C alongside them "
            "improves how much iron is absorbed."
        ),
    },
    "elevated_uric_acid": {
        "label": "Elevated uric acid",
        "triggers": [{"analyte": "uric_acid", "flag": "high"}],
        "severity": "moderate",
        "prefer_low": ["total_sugars_g", "added_sugars_g"],
        "prefer_high": [],
        "thresholds": {
            "total_sugars_g": {"good": 5, "bad": 22.5},
            "added_sugars_g": {"good": 2, "bad": 12},
        },
        "weights": {"total_sugars_g": 2.0, "added_sugars_g": 2.5},
        "explanation": (
            "Your uploaded results show uric acid above the usual range. "
            "Fructose from added sugars raises uric acid. Purine-rich foods "
            "matter too, but nutrition labels don't declare purines, so we "
            "can't check that here."
        ),
    },
    "vitamin_d_deficiency": {
        "label": "Low vitamin D",
        "triggers": [{"analyte": "vitamin_d", "flag": "low"}],
        "severity": "low",
        "prefer_low": [],
        "prefer_high": ["vitamin_d_mcg"],
        "thresholds": {"vitamin_d_mcg": {"good": 2.5, "bad": 0.2}},
        "weights": {"vitamin_d_mcg": 2.0},
        "explanation": (
            "Your uploaded results show vitamin D below the usual range. Few "
            "foods contain much, so fortified products are where it usually "
            "shows up on a label."
        ),
    },
    "elevated_liver_enzymes": {
        "label": "Elevated liver enzymes",
        "triggers": [
            {"analyte": "alt", "flag": "high"},
            {"analyte": "ast", "flag": "high"},
        ],
        "severity": "moderate",
        "prefer_low": ["added_sugars_g", "total_sugars_g", "saturated_fat_g"],
        "prefer_high": ["fiber_g"],
        "thresholds": {
            "added_sugars_g": {"good": 2, "bad": 12},
            "total_sugars_g": {"good": 5, "bad": 22.5},
            "saturated_fat_g": {"good": 1.5, "bad": 5},
            "fiber_g": {"good": 6, "bad": 1.5},
        },
        "weights": {
            "added_sugars_g": 2.5,
            "total_sugars_g": 2.0,
            "saturated_fat_g": 1.5,
            "fiber_g": 1.0,
        },
        "explanation": (
            "Your uploaded results show liver enzymes above the usual range. "
            "Added sugars and saturated fat are the dietary factors most often "
            "linked to this pattern."
        ),
    },
    "elevated_sodium": {
        "label": "Elevated sodium",
        "triggers": [{"analyte": "serum_sodium", "flag": "high"}],
        "severity": "low",
        "prefer_low": ["sodium_mg"],
        "prefer_high": [],
        "thresholds": {"sodium_mg": {"good": 120, "bad": 600}},
        "weights": {"sodium_mg": 2.0},
        "explanation": "Your uploaded results show sodium above the usual range.",
    },
}


# How readable a nutrient name is in a sentence.
NUTRIENT_LABELS = {
    "energy_kcal": "calories",
    "protein_g": "protein",
    "total_fat_g": "total fat",
    "saturated_fat_g": "saturated fat",
    "trans_fat_g": "trans fat",
    "cholesterol_mg": "cholesterol",
    "total_carbohydrates_g": "carbohydrates",
    "total_sugars_g": "sugars",
    "added_sugars_g": "added sugars",
    "fiber_g": "fibre",
    "sodium_mg": "sodium",
    "potassium_mg": "potassium",
    "iron_mg": "iron",
    "calcium_mg": "calcium",
    "vitamin_d_mcg": "vitamin D",
    "vitamin_c_mg": "vitamin C",
}


def nutrient_label(key: str) -> str:
    return NUTRIENT_LABELS.get(key, key.replace("_", " ").replace(" g", "").strip())


def resolve_conditions(markers: list[dict]) -> list[dict]:
    """
    Which conditions a set of confirmed markers activates.

    `markers` are dicts with at least `analyte` and `flag`, as stored. Only
    markers the user confirmed should be passed in — an unreviewed misread
    must never move a score.

    Returns condition profiles with the triggering markers attached, so the UI
    can show *why* each one applied rather than asserting it.
    """
    # Reduce to the most recent reading per analyte FIRST, and only then look
    # at whether it is abnormal. Filtering on the flag first would discard a
    # newer normal result before it could supersede an older abnormal one, so
    # a resolved condition would persist for as long as the old document did —
    # exactly the case someone re-tests to rule out.
    by_analyte: dict[str, dict] = {}
    for marker in markers:
        analyte = marker.get("analyte")
        if not analyte:
            continue
        existing = by_analyte.get(analyte)
        if existing is None or str(marker.get("measured_at") or "") >= str(
            existing.get("measured_at") or ""
        ):
            by_analyte[analyte] = marker

    by_analyte = {
        analyte: marker
        for analyte, marker in by_analyte.items()
        if marker.get("flag") in ("high", "low")
    }

    resolved = []
    for key, profile in HEALTH_CONDITION_PROFILES.items():
        matched = [
            by_analyte[t["analyte"]]
            for t in profile["triggers"]
            if t["analyte"] in by_analyte
            and by_analyte[t["analyte"]].get("flag") == t["flag"]
        ]
        if matched:
            resolved.append({**profile, "key": key, "matched_markers": matched})
    return resolved


def detect_goal_conflicts(
    goal_profiles: list[tuple[str, Optional[dict]]],
    conditions: list[dict],
) -> list[dict]:
    """
    Goals whose nutritional direction opposes a health condition's.

    This is the feature that motivated putting both in the same vocabulary. A
    goal wants some nutrients high; a condition wants some nutrients low. Where
    those sets intersect, the user is pulling in two directions at once:

        "muscle gain"              prefer_high: protein_g
        "reduced kidney function"  prefer_low:  protein_g
        →  conflict on protein

    It is set intersection, not inference — no model, no heuristics, no
    thresholds to tune. Every future goal and condition pair is covered the
    moment both are defined, because they share one vocabulary.

    `goal_profiles` is (goal_type, profile-or-None) so unscoreable goals are
    skipped rather than guessed at.
    """
    conflicts = []
    for goal_type, profile in goal_profiles:
        if not profile:
            continue
        goal_high = set(profile.get("prefer_high") or [])
        goal_low = set(profile.get("prefer_low") or [])

        for condition in conditions:
            cond_low = set(condition.get("prefer_low") or [])
            cond_high = set(condition.get("prefer_high") or [])

            # The goal pushes a nutrient up that the condition wants down, or
            # vice versa. Both directions are real conflicts.
            opposed = sorted((goal_high & cond_low) | (goal_low & cond_high))
            if not opposed:
                continue

            names = ", ".join(nutrient_label(n) for n in opposed)
            conflicts.append({
                "goal": goal_type,
                "goal_label": profile.get("label", goal_type),
                "condition_key": condition["key"],
                "condition_label": condition["label"],
                "nutrients": opposed,
                "severity": condition.get("severity", "moderate"),
                "description": (
                    f"Your “{profile.get('label', goal_type)}” goal aims to change "
                    f"{names} in the opposite direction to what your recent results "
                    f"suggest ({condition['label'].lower()}). That doesn't make the "
                    f"goal wrong — but it's worth raising with your doctor or a "
                    f"dietitian before pushing hard on it."
                ),
            })
    return conflicts
