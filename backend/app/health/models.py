"""
Health context storage.

Two decisions are visible in this schema, and both are deliberate.

**The uploaded document is never persisted.** There is no path column, no blob
column, nothing pointing at disk. A lab report is parsed in memory, the numbers
the user confirms are kept, and the file is discarded when the request ends.
That removes an entire category of risk at a stroke — no encrypted blob store
to manage, no retention policy to enforce, no chance of a PDF of someone's
blood work turning up under the public /uploads mount that serves product
photos. The markers are the useful part; the paper is not.

**Markers are stored as one encrypted blob, not as queryable columns.**
Encrypting only the numeric value would leave `analyte = "hba1c"` and
`flag = "high"` legible in the clear — which is the sensitive fact, not the
number. Because the payload is opaque, the only way to read health data is to
load a specific user's own rows, and the schema simply cannot answer "which
users have elevated glucose".

See app.health.crypto for the fail-closed key handling.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class HealthDocument(Base):
    """
    One uploaded health document, reduced to the markers read from it.

    Everything sensitive lives inside `markers_encrypted`. The columns beside
    it are metadata the user needs to manage their own uploads — when it was
    added, what they called it, how many readings it produced — and none of
    them reveal a result.
    """
    __tablename__ = "health_documents"
    __table_args__ = (
        Index("ix_health_documents_user", "user_id", "uploaded_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # The user's own label for it ("Annual checkup, March"). Defaults to the
    # filename, which is why it is treated as user-supplied text and shown
    # back only to its owner.
    title = Column(String(255), nullable=False)
    # "blood_test" | "other". Not a diagnosis, just what kind of paper it was.
    document_type = Column(String(50), nullable=False, default="blood_test")

    # Fernet-encrypted JSON: the list of markers, each with analyte, label,
    # value, unit, reference range, flag, measured_at and confirmed.
    markers_encrypted = Column(Text, nullable=False)

    # Safe to keep in the clear: a count reveals nothing about the results.
    marker_count = Column(Integer, nullable=False, default=0)
    # How the numbers were read: "pdf_text" | "vision" | "manual".
    extraction_source = Column(String(30), nullable=False, default="manual")
    # Which model read it, for provenance. Never a result.
    extraction_model = Column(String(120), nullable=True)

    # Whether the user has reviewed and confirmed the readings. Nothing
    # unconfirmed is allowed to influence a product score.
    confirmed_at = Column(DateTime(timezone=True), nullable=True)

    uploaded_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user = relationship("User", back_populates="health_documents")
