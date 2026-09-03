"""
Community database models.

Covers: community_reviews, review_context, review_experiences
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, Enum as SAEnum, JSON
)
from sqlalchemy.orm import relationship
from app.db.session import Base
import enum


class UsageDuration(str, enum.Enum):
    ONCE = "once"
    FEW_DAYS = "few_days"
    FEW_WEEKS = "few_weeks"
    SEVERAL_MONTHS = "several_months"
    MORE_THAN_A_YEAR = "more_than_a_year"


class ExperienceType(str, enum.Enum):
    NO_NOTICEABLE_EFFECT = "no_noticeable_effect"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    DIGESTIVE_DISCOMFORT = "digestive_discomfort"
    SKIN_REACTION = "skin_reaction"
    HEADACHE = "headache"
    ENERGY_CHANGE = "energy_change"
    APPETITE_CHANGE = "appetite_change"
    OTHER = "other"


class CommunityReview(Base):
    __tablename__ = "community_reviews"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    product_version_id = Column(Integer, ForeignKey("product_versions.id"), nullable=True)

    # Experience
    usage_duration = Column(SAEnum(UsageDuration), nullable=False)
    experience_type = Column(SAEnum(ExperienceType), nullable=False)
    experience_text = Column(Text, nullable=True)
    rating = Column(Integer, nullable=True)  # 1-5

    # Moderation
    is_approved = Column(Boolean, default=False)
    is_flagged = Column(Boolean, default=False)
    flag_reason = Column(String(255), nullable=True)
    helpful_count = Column(Integer, default=0)
    not_helpful_count = Column(Integer, default=0)

    # Timestamps
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", back_populates="community_reviews")
    product = relationship("Product", back_populates="community_reviews")
    anonymous_context = relationship("ReviewContext", back_populates="review", uselist=False, cascade="all, delete-orphan")


class ReviewContext(Base):
    """Anonymous context optionally shared with a review for similarity matching."""
    __tablename__ = "review_contexts"

    id = Column(Integer, primary_key=True, index=True)
    review_id = Column(Integer, ForeignKey("community_reviews.id", ondelete="CASCADE"), unique=True, nullable=False)
    age_range = Column(String(20), nullable=True)
    dietary_pattern = Column(String(50), nullable=True)
    activity_level = Column(String(50), nullable=True)
    goals = Column(JSON, nullable=True)
    relevant_allergies = Column(JSON, nullable=True)
    usage_duration = Column(String(50), nullable=True)

    review = relationship("CommunityReview", back_populates="anonymous_context")
