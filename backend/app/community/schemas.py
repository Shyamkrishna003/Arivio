"""
Community Pydantic schemas.

Note what is deliberately absent from every response: the reviewer's identity.
Experiences are contributed under a private profile, and the requirement
separates identity from community contribution — a review carries its
anonymised context and nothing that identifies who wrote it.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.community.models import ExperienceType, UsageDuration


class ReviewContextResponse(BaseModel):
    """The anonymised context an author consented to share, if any."""
    age_range: Optional[str] = None
    dietary_pattern: Optional[str] = None
    activity_level: Optional[str] = None
    goals: Optional[list[str]] = None
    relevant_allergies: Optional[list[str]] = None
    usage_duration: Optional[str] = None

    model_config = {"from_attributes": True}


class ReviewCreate(BaseModel):
    product_id: int
    usage_duration: UsageDuration
    experience_type: ExperienceType
    experience_text: Optional[str] = Field(None, max_length=4000)
    rating: Optional[int] = Field(None, ge=1, le=5)
    # Opt-in per submission, and still subject to the account's privacy
    # settings — a review can never share more than the profile permits.
    share_context: bool = False


class RelevanceResponse(BaseModel):
    """
    Why an experience may be relevant to the reader.

    `score` is included for transparency but is never the whole story: the
    requirement is explicit that a bare "Similarity: 87%" is not an acceptable
    explanation, so `reasons` and `differences` always accompany it.
    """
    score: int                       # 0-100
    reasons: list[str] = []          # dimensions that agreed
    differences: list[str] = []      # dimensions that did not — never hidden
    not_compared: list[str] = []     # dimensions neither side disclosed


class ReviewResponse(BaseModel):
    id: int
    product_id: int
    usage_duration: str
    experience_type: str
    experience_text: Optional[str] = None
    rating: Optional[int] = None
    helpful_count: int = 0
    not_helpful_count: int = 0
    created_at: datetime

    # Present only on the reader's own review, so the UI can offer to withdraw
    # it and can explain a review being held.
    is_mine: bool = False
    is_published: bool = True
    moderation_note: Optional[str] = None

    # How the reader voted, so the UI reflects their own action.
    my_vote: Optional[bool] = None

    shared_context: Optional[ReviewContextResponse] = None
    relevance: Optional[RelevanceResponse] = None

    model_config = {"from_attributes": True}


class ExperienceBreakdownItem(BaseModel):
    experience_type: str
    label: str
    count: int
    percentage: float


class CommunitySummary(BaseModel):
    """
    Aggregated experiences for a product.

    Overall and personalized figures are reported separately, as the
    requirement asks — a reader must be able to tell "what everyone reported"
    from "what people like me reported".
    """
    total_experiences: int
    breakdown: list[ExperienceBreakdownItem] = []
    average_rating: Optional[float] = None

    # Personalized view. `relevant_experiences` counts only those whose shared
    # context clears the relevance threshold against this reader.
    relevant_experiences: int = 0
    relevant_breakdown: list[ExperienceBreakdownItem] = []

    # Stated with every aggregate. Community reports describe personal
    # experience and do not establish causation.
    disclaimer: str = (
        "These figures describe personal experiences reported by users. "
        "They do not establish that this product causes any effect."
    )
    # True when there is too little data to report percentages honestly.
    insufficient_data: bool = False


class ReviewListResponse(BaseModel):
    product_id: int
    summary: CommunitySummary
    # Experiences from users with a comparable profile, most relevant first.
    relevant_reviews: list[ReviewResponse] = []
    # Everything else, most recent first.
    recent_reviews: list[ReviewResponse] = []
    my_review: Optional[ReviewResponse] = None


class VoteCreate(BaseModel):
    is_helpful: bool


class FlagCreate(BaseModel):
    reason: Optional[str] = Field(None, max_length=255)
