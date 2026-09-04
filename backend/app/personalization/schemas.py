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
    preference_conflict: str = "none"
    preference_adjustment: int = 0
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
