"""
Allergen inference cache.

When a user declares an allergen we have no synonym coverage for, string
matching cannot settle whether the product contains it. We ask an LLM to read
the ingredient list instead — and cache the verdict per (allergen, product) so
that costs one call ever rather than one per page view.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, UniqueConstraint, Index
)
from app.db.session import Base


class AllergenInference(Base):
    """A cached AI verdict on whether one product contains one allergen."""
    __tablename__ = "allergen_inferences"
    # Verdicts from different models / prompt revisions coexist, so switching
    # AI_MODEL and switching back does not force a re-run. The ingredient hash
    # is deliberately NOT part of the key: a changed label should replace the
    # stale verdict, not accumulate alongside it.
    __table_args__ = (
        UniqueConstraint(
            "allergen", "product_id", "model", "prompt_version",
            name="uq_allergen_inference",
        ),
        Index("ix_allergen_inference_lookup", "product_id", "allergen"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # Normalized allergen name (app.personalization.engine._normalize).
    allergen = Column(String(100), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)

    # SHA-256 of the ingredients and declared allergens this verdict was
    # reached from. A product whose label is re-scraped gets a new hash, so the
    # stale verdict stops matching and is recomputed rather than trusted.
    ingredients_hash = Column(String(64), nullable=False)

    found = Column(Boolean, nullable=False)
    # "high" | "medium" | "low" — high maps to a confirmed conflict, anything
    # weaker to a trace-level warning.
    certainty = Column(String(20), nullable=False, default="medium")
    matched_ingredient = Column(String(255), nullable=True)
    reason = Column(Text, nullable=True)

    # Which model and prompt revision produced this. Both are part of the cache
    # key: a different model may reach a different verdict, and editing the
    # prompt changes what we asked. Bump PROMPT_VERSION in inference.py when
    # the prompt changes materially.
    model = Column(String(100), nullable=False)
    prompt_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
