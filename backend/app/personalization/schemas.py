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
    unscored_goals: list[str] = []
    weights: dict


class SuitabilityResponse(BaseModel):
    """Full suitability analysis response."""
    overall_score: int
    verdict: str
    confidence: int
    allergen_safe: bool
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
