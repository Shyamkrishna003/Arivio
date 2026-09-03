"""
AI module Pydantic schemas for request/response validation.
"""

from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AIReportResponse(BaseModel):
    """Response schema for the AI-generated product report."""
    summary: str
    detailed_analysis: str
    key_insights: list[str]
    recommendations: list[str]
    confidence_note: str
    provider: str
    model: str


class ReportFeedbackCreate(BaseModel):
    """Schema for submitting feedback on an AI report."""
    product_id: int
    rating: int                       # 1-5 stars
    feedback_type: str = "general"    # "helpful", "inaccurate", "incomplete", "general"
    comment: Optional[str] = None


class ReportFeedbackResponse(BaseModel):
    """Response schema for saved report feedback."""
    id: int
    product_id: int
    user_id: int
    rating: int
    feedback_type: str
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
