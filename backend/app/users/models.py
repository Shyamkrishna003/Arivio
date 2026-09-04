"""
User database models.

Covers: users, user_profiles, user_goals, user_preferences,
user_allergies, user_health_context, privacy_settings
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Text,
    ForeignKey, Enum as SAEnum, JSON, Index, text
)
from sqlalchemy.orm import relationship
from app.db.session import Base
import enum


class UserRole(str, enum.Enum):
    USER = "user"
    EXPERT = "expert"
    ADMIN = "admin"


class ActivityLevel(str, enum.Enum):
    SEDENTARY = "sedentary"
    LIGHTLY_ACTIVE = "lightly_active"
    MODERATELY_ACTIVE = "moderately_active"
    VERY_ACTIVE = "very_active"
    EXTREMELY_ACTIVE = "extremely_active"


class DietaryPattern(str, enum.Enum):
    OMNIVORE = "omnivore"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    EGGETARIAN = "eggetarian"
    PESCATARIAN = "pescatarian"
    KETO = "keto"
    OTHER = "other"


# The age bands offered in the UI. Kept as a constant rather than free text
# because community relevance compares them on an ordered scale — a value
# outside this set cannot be placed on that scale and silently stops
# contributing to the match.
AGE_RANGES = ["under 18", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]


class AllergyType(str, enum.Enum):
    ALLERGY = "allergy"
    INTOLERANCE = "intolerance"
    PREFERENCE = "preference"


class GoalProfileStatus(str, enum.Enum):
    """
    Whether a goal can actually be scored.

    Stored as a plain string column rather than a DB enum so new states can be
    added without an ALTER TYPE migration.
    """
    PENDING = "pending"          # profile is being generated in the background
    READY = "ready"              # a hardcoded or custom profile exists
    UNSUPPORTED = "unsupported"  # not a scoreable nutritional goal


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    role = Column(SAEnum(UserRole), default=UserRole.USER, nullable=False)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    profile = relationship("UserProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    goals = relationship("UserGoal", back_populates="user", cascade="all, delete-orphan")
    preferences = relationship("UserPreference", back_populates="user", cascade="all, delete-orphan")
    allergies = relationship("UserAllergy", back_populates="user", cascade="all, delete-orphan")
    privacy_settings = relationship("PrivacySetting", back_populates="user", uselist=False, cascade="all, delete-orphan")
    community_reviews = relationship("CommunityReview", back_populates="user", cascade="all, delete-orphan")
    saved_products = relationship("SavedProduct", back_populates="user", cascade="all, delete-orphan")
    scan_history = relationship("UserScanHistory", back_populates="user", cascade="all, delete-orphan")


class UserScanHistory(Base):
    __tablename__ = "user_scan_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    scanned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="scan_history")
    product = relationship("app.products.models.Product")

class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    age_range = Column(String(20), nullable=True)  # e.g., "18-24", "25-34"
    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    activity_level = Column(SAEnum(ActivityLevel), nullable=True)
    dietary_pattern = Column(SAEnum(DietaryPattern), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="profile")


class UserGoal(Base):
    __tablename__ = "user_goals"
    # A duplicate goal is counted twice in the weighted goal average, so the
    # same goal cannot be active twice for one user. Partial, so removing a
    # goal does not block re-adding it later. The add_goal endpoint does more
    # than this — it also rejects aliases of an existing goal ("weight loss"
    # vs "weight management"), which the stored text alone cannot express.
    __table_args__ = (
        Index(
            "uq_user_goal_active",
            "user_id", "goal_type",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    goal_type = Column(String(100), nullable=False)  # normalized, e.g. "muscle gain"
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    # Whether this goal can be scored, and a user-facing explanation when it cannot.
    profile_status = Column(String(20), default=GoalProfileStatus.PENDING.value, nullable=False)
    status_message = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="goals")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    preference_type = Column(String(100), nullable=False)  # e.g., "low_sugar", "high_protein"
    is_hard_constraint = Column(Boolean, default=False)  # Hard constraint vs soft preference
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="preferences")


class UserAllergy(Base):
    __tablename__ = "user_allergies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    allergen = Column(String(100), nullable=False)  # e.g., "milk", "peanuts", "gluten"
    allergy_type = Column(SAEnum(AllergyType), nullable=False)
    severity = Column(String(50), nullable=True)  # e.g., "mild", "moderate", "severe"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="allergies")


class PrivacySetting(Base):
    __tablename__ = "privacy_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    allow_anonymous_context_sharing = Column(Boolean, default=False)
    share_age_range = Column(Boolean, default=False)
    share_dietary_pattern = Column(Boolean, default=False)
    share_activity_level = Column(Boolean, default=False)
    share_goals = Column(Boolean, default=False)
    share_allergies = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="privacy_settings")


class CustomGoalProfile(Base):
    __tablename__ = "custom_goal_profiles"

    id = Column(Integer, primary_key=True, index=True)
    goal_type = Column(String(100), unique=True, index=True, nullable=False)
    label = Column(String(100), nullable=False)
    prefer_low = Column(JSON, nullable=False)  # List of nutrient keys
    prefer_high = Column(JSON, nullable=False) # List of nutrient keys
    thresholds = Column(JSON, nullable=False)  # Dict mapping nutrient -> {"good": X, "bad": Y}
    weights = Column(JSON, nullable=True)      # Dict mapping nutrient -> weight
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

import app.community.models
import app.products.models
import app.ingredients.models
import app.allergens.models
