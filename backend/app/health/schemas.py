"""Health context request/response schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class MarkerPayload(BaseModel):
    """
    One reading, as stored and as edited on the confirmation screen.

    `analyte` is our normalised key and is set by the server, never trusted
    from the client: it decides which thresholds a value is scored against,
    so letting a caller pick it would let them attach any number to any rule.
    """
    label: str = Field(..., max_length=200)
    value: Optional[float] = None
    unit: Optional[str] = Field(None, max_length=40)
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    measured_at: Optional[str] = Field(None, max_length=40)
    # Only markers the user has ticked influence a product score.
    confirmed: bool = False

    # Server-derived, echoed back for display.
    analyte: Optional[str] = None
    flag: str = "unknown"
    recognized: bool = False
    unit_converted: bool = False
    note: Optional[str] = None

    @field_validator("label", "unit", "measured_at")
    @classmethod
    def strip_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class HealthDocumentSummary(BaseModel):
    """List-view metadata. Deliberately carries no results."""
    id: int
    title: str
    document_type: str
    marker_count: int
    extraction_source: str
    extraction_model: Optional[str] = None
    confirmed: bool
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class ConditionSummary(BaseModel):
    """A pattern the markers activated — never a diagnosis."""
    key: str
    label: str
    severity: str
    explanation: str
    # Which readings triggered it, so the user can see why rather than being
    # told to trust it.
    triggered_by: List[str] = []
    watch_nutrients: List[str] = []
    prefer_nutrients: List[str] = []


class GoalConflict(BaseModel):
    goal: str
    goal_label: str
    condition_key: str
    condition_label: str
    nutrients: List[str]
    severity: str
    description: str


class HealthDocumentDetail(HealthDocumentSummary):
    markers: List[MarkerPayload] = []
    warnings: List[str] = []


class HealthExtractionResponse(BaseModel):
    """What an upload produced, pending the user's confirmation."""
    document: HealthDocumentDetail
    warnings: List[str] = []


class HealthConfirmRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=255)
    markers: List[MarkerPayload]


class HealthContextResponse(BaseModel):
    """The user's whole health context, as their profile page shows it."""
    enabled: bool
    consent_given: bool
    documents: List[HealthDocumentSummary] = []
    conditions: List[ConditionSummary] = []
    goal_conflicts: List[GoalConflict] = []
    # Present when the feature is switched off server-side, so the UI can say
    # why the section is unavailable instead of showing an empty state.
    unavailable_reason: Optional[str] = None


class ConsentRequest(BaseModel):
    consent: bool
