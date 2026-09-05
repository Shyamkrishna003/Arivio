"""
Personalization Pydantic schemas for request/response validation.
"""

from pydantic import BaseModel
from typing import Optional
from app.products.schemas import ProductResponse


class SuitabilityFlagResponse(BaseModel):
    flag_type: str       # "danger", "warning", "positive", "info"
    category: str        # "allergen", "nutrition", "ingredient", "goal"
    title: str
    description: str
    impact: int


class GoalAlignmentResponse(BaseModel):
    goal: str
    alignment: str              # "excellent"|"good"|"neutral"|"poor"|"bad"|"not_evaluated"
    # None when the goal could not be scored — deliberately not a placeholder
    # number, which would read as a real verdict.
    score: Optional[int] = None
    reason: str
    evaluated: bool = True


class SuitabilityBreakdown(BaseModel):
    # None when no goal could be evaluated — the goal term is dropped from the
    # composite and the remaining weights are renormalized.
    goal_alignment: Optional[int] = None
    nutritional_quality: int
    ingredient_profile: int
    allergen_conflict: str = "none"
    # "none" | "uncertain" | "incompatible" — whether the product clashes with
    # the user's declared dietary pattern.
    diet_conflict: str = "none"
    # "none" | "soft" | "strict" — whether a nutrient preference was missed,
    # and the net points preferences moved the score by.
    # "none" | "severe" | "extreme" — a nutrient present in a disqualifying
    # amount (2x or 3x the level considered high). Caps the score outright
    # rather than deducting, so several mild positives cannot outvote it.
    nutrient_extreme: str = "none"
    # Which category profile shaped the scoring ("beverages", "added_fats"),
    # or null for the ordinary path. Present so the UI can explain why a
    # product was judged on a different basis.
    category: Optional[str] = None
    # Grams of a realistic serving for that category, where one is known.
    # Null means the product was judged on the per-100g basis unchanged.
    reference_portion_g: Optional[float] = None
    preference_conflict: str = "none"
    preference_adjustment: int = 0
    # "none" | "watch" | "avoid" — whether the product works against a marker
    # from the user's uploaded health documents, and the net points it moved
    # the score by. Absent for users with no confirmed health context.
    health_conflict: str = "none"
    health_adjustment: int = 0
    unscored_goals: list[str] = []
    weights: dict


class SuitabilityResponse(BaseModel):
    """Full suitability analysis response."""
    overall_score: int
    verdict: str
    confidence: int
    allergen_safe: bool
    # False when the product contains something the dietary pattern excludes.
    # Reported separately from allergen safety: one is a compatibility
    # question, the other a safety one.
    diet_compatible: bool = True
    flags: list[SuitabilityFlagResponse]
    goal_alignments: list[GoalAlignmentResponse]
    nutritional_quality_score: int
    ingredient_profile_score: int
    breakdown: SuitabilityBreakdown

class AlternativeProduct(BaseModel):
    product: ProductResponse
    suitability_score: int
    verdict: str
    match_reasons: list[str]

class AlternativesResponse(BaseModel):
    original_product_id: int
    category: str
    alternatives: list[AlternativeProduct]
