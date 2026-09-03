"""
Ingredient database models.

Covers: ingredients, ingredient_aliases, ingredient_claims
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, Enum as SAEnum, JSON
)
from sqlalchemy.orm import relationship
from app.db.session import Base
import enum


class EvidenceStrength(str, enum.Enum):
    VERY_HIGH = "very_high"
    HIGH = "high"
    MODERATE = "moderate"
    LIMITED = "limited"
    VERY_LIMITED = "very_limited"
    UNKNOWN = "unknown"


class Ingredient(Base):
    __tablename__ = "ingredients"

    id = Column(Integer, primary_key=True, index=True)
    canonical_name = Column(String(255), unique=True, nullable=False, index=True)
    category = Column(String(100), nullable=True)  # "sweetener", "preservative", "emulsifier", etc.
    function = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    common_uses = Column(JSON, nullable=True)
    potential_benefits = Column(JSON, nullable=True)
    potential_concerns = Column(JSON, nullable=True)
    population_considerations = Column(JSON, nullable=True)
    regulatory_status = Column(JSON, nullable=True)  # {"india": "approved", "eu": "restricted"}
    is_common_allergen = Column(Boolean, default=False)
    allergen_group = Column(String(100), nullable=True)
    evidence_strength = Column(SAEnum(EvidenceStrength), default=EvidenceStrength.UNKNOWN)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    aliases = relationship("IngredientAlias", back_populates="ingredient", cascade="all, delete-orphan")
    claims = relationship("IngredientClaim", back_populates="ingredient", cascade="all, delete-orphan")
    product_ingredients = relationship("ProductIngredient", back_populates="ingredient")


class IngredientAlias(Base):
    __tablename__ = "ingredient_aliases"

    id = Column(Integer, primary_key=True, index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False)
    alias_name = Column(String(255), nullable=False, index=True)
    language = Column(String(10), default="en")

    ingredient = relationship("Ingredient", back_populates="aliases")


class IngredientClaim(Base):
    """Structured claims about an ingredient, backed by evidence."""
    __tablename__ = "ingredient_claims"

    id = Column(Integer, primary_key=True, index=True)
    ingredient_id = Column(Integer, ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False)
    claim_text = Column(Text, nullable=False)
    claim_type = Column(String(100), nullable=True)  # "benefit", "concern", "neutral"
    population = Column(String(255), nullable=True)  # "general", "children", "pregnant"
    evidence_strength = Column(SAEnum(EvidenceStrength), default=EvidenceStrength.UNKNOWN)
    confidence = Column(Integer, nullable=True)  # 0-100
    source_count = Column(Integer, default=0)
    contradictory_evidence = Column(Boolean, default=False)
    sources = Column(JSON, nullable=True)
    last_reviewed = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    ingredient = relationship("Ingredient", back_populates="claims")
