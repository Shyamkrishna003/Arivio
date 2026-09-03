"""
User profile schemas.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ProfileUpdate(BaseModel):
    age_range: Optional[str] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    activity_level: Optional[str] = None
    dietary_pattern: Optional[str] = None


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
    preference_type: str = Field(..., max_length=100)
    is_hard_constraint: bool = False


class PreferenceResponse(BaseModel):
    id: int
    preference_type: str
    is_hard_constraint: bool

    model_config = {"from_attributes": True}


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
