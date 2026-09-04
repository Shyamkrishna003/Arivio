"""
User profile schemas.
"""

from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from datetime import datetime

from app.users.models import AGE_RANGES, ActivityLevel, DietaryPattern
from app.personalization.engine import PREFERENCE_PROFILES

PREFERENCE_KEYS = tuple(PREFERENCE_PROFILES.keys())


class ProfileUpdate(BaseModel):
    # Typed against the enums rather than bare strings: an unknown value used to
    # pass validation and then fail at flush time as a 500, when it is really a
    # bad request. The age band is constrained for the same reason — an
    # off-scale value stops contributing to community relevance without saying so.
    age_range: Optional[Literal[tuple(AGE_RANGES)]] = None  # type: ignore[valid-type]
    height_cm: Optional[float] = Field(None, gt=0, le=300)
    weight_kg: Optional[float] = Field(None, gt=0, le=700)
    activity_level: Optional[ActivityLevel] = None
    dietary_pattern: Optional[DietaryPattern] = None


class ProfileResponse(BaseModel):
    id: int
    age_range: Optional[str]
    height_cm: Optional[float]
    weight_kg: Optional[float]
    activity_level: Optional[str]
    dietary_pattern: Optional[str]

    model_config = {"from_attributes": True}


class GoalCreate(BaseModel):
    goal_type: str = Field(..., max_length=100)
    priority: int = 0


class GoalResponse(BaseModel):
    id: int
    goal_type: str
    priority: int
    is_active: bool
    # "pending" | "ready" | "unsupported" — whether this goal can be scored,
    # plus a user-facing explanation when it cannot.
    profile_status: str = "ready"
    status_message: Optional[str] = None

    model_config = {"from_attributes": True}


from app.users.models import AllergyType

class AllergyCreate(BaseModel):
    allergen: str = Field(..., max_length=100)
    allergy_type: AllergyType
    severity: Optional[str] = None


class AllergyResponse(BaseModel):
    id: int
    allergen: str
    allergy_type: str
    severity: Optional[str]

    model_config = {"from_attributes": True}


class PreferenceCreate(BaseModel):
    # Constrained to the nutrients the engine can actually measure. Free text
    # used to be accepted and then silently ignored at scoring time, which is
    # worse than refusing it: the user believes a preference is being applied.
    preference_type: Literal[tuple(PREFERENCE_KEYS)]  # type: ignore[valid-type]
    # Escalates the preference from a nudge to a hard constraint.
    is_hard_constraint: bool = False


class PreferenceResponse(BaseModel):
    id: int
    preference_type: str
    is_hard_constraint: bool

    model_config = {"from_attributes": True}


class PrivacyResponse(BaseModel):
    """
    What the account permits to be shared anonymously with a community review.

    `allow_anonymous_context_sharing` is the master switch — with it off,
    nothing below it is shared regardless of the individual flags.
    """
    allow_anonymous_context_sharing: bool = False
    share_age_range: bool = False
    share_dietary_pattern: bool = False
    share_activity_level: bool = False
    share_goals: bool = False
    share_allergies: bool = False

    model_config = {"from_attributes": True}


class PrivacyUpdate(BaseModel):
    allow_anonymous_context_sharing: Optional[bool] = None
    share_age_range: Optional[bool] = None
    share_dietary_pattern: Optional[bool] = None
    share_activity_level: Optional[bool] = None
    share_goals: Optional[bool] = None
    share_allergies: Optional[bool] = None


class FullProfileResponse(BaseModel):
    user_id: int
    profile: Optional[ProfileResponse]
    goals: List[GoalResponse]
    preferences: List[PreferenceResponse]
    allergies: List[AllergyResponse]

class RecentActivityItem(BaseModel):
    id: int
    product_id: int
    product_name: str
    product_brand: Optional[str] = None
    product_image_url: Optional[str] = None
    scanned_at: datetime

    model_config = {"from_attributes": True}

class DashboardResponse(BaseModel):
    total_scans: int
    saved_products: int
    active_goals: int
    recent_activity: List[RecentActivityItem]
