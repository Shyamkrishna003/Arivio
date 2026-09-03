"""
AI module database models.

Covers: report_feedback — user ratings and comments on AI-generated reports.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, DateTime, Text,
    ForeignKey
)
from sqlalchemy.orm import relationship
from app.db.session import Base


class ReportFeedback(Base):
    """Stores user feedback on AI-generated product reports."""
    __tablename__ = "report_feedback"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    rating = Column(Integer, nullable=False)         # 1-5 stars
    feedback_type = Column(String(50), default="general")  # "helpful", "inaccurate", "incomplete", "general"
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User")
    product = relationship("Product")
