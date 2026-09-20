"""
Health context API.

Gated three ways, all of which must pass before a document is even parsed:

  1. **Encryption available.** No key, no feature (503). Health data is never
     written in plaintext because a config value was missed.
  2. **Explicit consent.** PRD §10 requires it, and it is recorded with a
     timestamp rather than implied by the act of uploading.
  3. **Authenticated owner.** Every query is scoped to the calling user, and
     there is no endpoint anywhere that returns another user's health data.

Withdrawing consent deletes the documents. Not hides, not deactivates —
deletes. That is what the consent screen promises, so it is what the code has
to do.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import get_current_user
from app.core.uploads import read_upload_capped
from app.db.session import get_db
from app.health.analytes import ANALYTES, flag_for, normalize_analyte
from app.health.crypto import (
    HealthEncryptionUnavailable, decrypt_json, encrypt_json, is_available,
    unavailable_reason,
)
from app.health.models import HealthDocument
from app.health.profiles import (
    detect_goal_conflicts, nutrient_label, resolve_conditions,
)
from app.health.schemas import (
    ConditionSummary, ConsentRequest, GoalConflict, HealthConfirmRequest,
    HealthContextResponse, HealthDocumentDetail, HealthDocumentSummary,
    HealthExtractionResponse, MarkerPayload,
)
from app.users.models import PrivacySetting, User

router = APIRouter(prefix="/health", tags=["Health Context"])

settings = get_settings()

ACCEPTED_TYPES = {
    "application/pdf",
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/tiff",
}


# ──────────────────────────────────────────────
# Gates
# ──────────────────────────────────────────────

def _require_encryption() -> None:
    if not is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Health documents are turned off on this server because no "
                "encryption key is configured."
            ),
        )


async def _privacy_row(db: AsyncSession, user_id: int) -> Optional[PrivacySetting]:
    return (await db.execute(
        select(PrivacySetting).where(PrivacySetting.user_id == user_id)
    )).scalar_one_or_none()


async def _require_consent(db: AsyncSession, user_id: int) -> None:
    row = await _privacy_row(db, user_id)
    if row is None or row.health_context_consent_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Please give consent for health-document processing before "
                "uploading a report."
            ),
        )


# ──────────────────────────────────────────────
# Marker helpers
# ──────────────────────────────────────────────

def _load_markers(document: HealthDocument) -> list[dict]:
    """
    Decrypt one document's markers.

    A row we cannot decrypt (written under a different key) yields nothing
    rather than raising: it must not take down the profile page, and it must
    certainly not reach the scoring engine as partial data.
    """
    try:
        markers = decrypt_json(document.markers_encrypted)
    except HealthEncryptionUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — InvalidToken and friends
        print(f"⚠️ Health document {document.id} could not be decrypted: {e}")
        return []
    return markers if isinstance(markers, list) else []


async def confirmed_markers_for_user(db: AsyncSession, user_id: int) -> list[dict]:
    """
    Every confirmed marker this user holds.

    The single entry point the rest of the application uses to read health
    context, so the "only confirmed markers count" rule is enforced in one
    place rather than remembered at each call site.
    """
    if not is_available():
        return []

    rows = (await db.execute(
        select(HealthDocument)
        .where(
            HealthDocument.user_id == user_id,
            HealthDocument.confirmed_at.isnot(None),
        )
        .order_by(HealthDocument.uploaded_at.desc())
    )).scalars().all()

    markers: list[dict] = []
    for document in rows:
        for marker in _load_markers(document):
            if marker.get("confirmed") and marker.get("recognized"):
                markers.append(marker)
    return markers


def _rescore_marker(payload: MarkerPayload) -> dict:
    """
    Re-derive the server-owned fields from a user-edited marker.

    The client may change the label, the value or the range; it may not decide
    which analyte those belong to or whether the result is flagged. Those
    determine which thresholds apply, so they are computed here from the
    submitted label, every time.
    """
    analyte = normalize_analyte(payload.label or "")
    marker = {
        "analyte": analyte,
        "label": payload.label,
        "value": payload.value,
        "unit": payload.unit or (ANALYTES[analyte].unit if analyte else None),
        "ref_low": payload.ref_low,
        "ref_high": payload.ref_high,
        "measured_at": payload.measured_at,
        "recognized": analyte is not None,
        "unit_converted": payload.unit_converted,
        "note": payload.note,
        "confirmed": bool(payload.confirmed),
        "flag": "unknown",
    }
    if analyte and isinstance(payload.value, (int, float)):
        marker["flag"] = flag_for(analyte, float(payload.value), payload.ref_low, payload.ref_high)
    return marker


def _to_detail(document: HealthDocument, markers: list[dict], warnings=None) -> HealthDocumentDetail:
    return HealthDocumentDetail(
        id=document.id,
        title=document.title,
        document_type=document.document_type,
        marker_count=document.marker_count,
        extraction_source=document.extraction_source,
        extraction_model=document.extraction_model,
        confirmed=document.confirmed_at is not None,
        uploaded_at=document.uploaded_at,
        markers=[MarkerPayload(**m) for m in markers],
        warnings=warnings or [],
    )


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.get("/context", response_model=HealthContextResponse)
async def get_health_context(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    The user's own health context: documents, the patterns they activate, and
    any goal that pulls against them.

    Answers for the calling user only. There is no variant of this endpoint
    that takes a user id.
    """
    if not is_available():
        return HealthContextResponse(
            enabled=False,
            consent_given=False,
            unavailable_reason=(
                "Health documents are turned off on this server because no "
                "encryption key is configured."
            ),
        )

    privacy = await _privacy_row(db, current_user.id)
    consent = privacy is not None and privacy.health_context_consent_at is not None

    documents = (await db.execute(
        select(HealthDocument)
        .where(HealthDocument.user_id == current_user.id)
        .order_by(HealthDocument.uploaded_at.desc())
    )).scalars().all()

    markers = await confirmed_markers_for_user(db, current_user.id)
    conditions = resolve_conditions(markers)

    conflicts = await goal_conflicts_for_user(db, current_user.id, conditions)

    return HealthContextResponse(
        enabled=True,
        consent_given=consent,
        documents=[HealthDocumentSummary(
            id=d.id, title=d.title, document_type=d.document_type,
            marker_count=d.marker_count, extraction_source=d.extraction_source,
            extraction_model=d.extraction_model,
            confirmed=d.confirmed_at is not None, uploaded_at=d.uploaded_at,
        ) for d in documents],
        conditions=[ConditionSummary(
            key=c["key"],
            label=c["label"],
            severity=c["severity"],
            explanation=c["explanation"],
            triggered_by=[
                f"{m['label']}: {m['value']}{(' ' + m['unit']) if m.get('unit') else ''} ({m['flag']})"
                for m in c["matched_markers"]
            ],
            watch_nutrients=[nutrient_label(n) for n in c.get("prefer_low", [])],
            prefer_nutrients=[nutrient_label(n) for n in c.get("prefer_high", [])],
        ) for c in conditions],
        goal_conflicts=[GoalConflict(**gc) for gc in conflicts],
    )


async def goal_conflicts_for_user(
    db: AsyncSession, user_id: int, conditions: Optional[list[dict]] = None
) -> list[dict]:
    """
    Goals that pull against the user's health context.

    Shared by the health context endpoint and the profile page, so the goals
    section and the health section can never disagree about what conflicts.
    """
    if conditions is None:
        conditions = resolve_conditions(await confirmed_markers_for_user(db, user_id))
    if not conditions:
        return []

    from app.personalization.engine import (
        GOAL_PROFILES, _normalize, _resolve_goal_key,
    )
    from app.users.models import CustomGoalProfile, UserGoal

    goals = (await db.execute(
        select(UserGoal).where(UserGoal.user_id == user_id, UserGoal.is_active == True)
    )).scalars().all()
    if not goals:
        return []

    custom = {}
    keys = list({_normalize(g.goal_type) for g in goals})
    for cp in (await db.execute(
        select(CustomGoalProfile).where(CustomGoalProfile.goal_type.in_(keys))
    )).scalars().all():
        custom[cp.goal_type] = {
            "label": cp.label,
            "prefer_low": cp.prefer_low,
            "prefer_high": cp.prefer_high,
        }

    pairs = []
    for goal in goals:
        norm = _normalize(goal.goal_type)
        profile = (
            custom.get(norm)
            or custom.get(_resolve_goal_key(goal.goal_type))
            or GOAL_PROFILES.get(_resolve_goal_key(goal.goal_type))
        )
        pairs.append((goal.goal_type, profile))

    return detect_goal_conflicts(pairs, conditions)


@router.put("/consent", response_model=HealthContextResponse)
async def set_consent(
    data: ConsentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Give or withdraw consent for health-document processing.

    Withdrawing deletes every document. The consent screen says the data is
    removed if you change your mind, so leaving rows behind — even inert ones —
    would make that a lie.
    """
    _require_encryption()

    privacy = await _privacy_row(db, current_user.id)
    if privacy is None:
        privacy = PrivacySetting(user_id=current_user.id)
        db.add(privacy)
        await db.flush()

    if data.consent:
        if privacy.health_context_consent_at is None:
            privacy.health_context_consent_at = datetime.now(timezone.utc)
    else:
        privacy.health_context_consent_at = None
        await db.execute(
            delete(HealthDocument).where(HealthDocument.user_id == current_user.id)
        )

    await db.flush()
    return await get_health_context(current_user, db)


@router.post("/documents", response_model=HealthExtractionResponse)
async def upload_document(
    file: UploadFile = File(..., description="Lab report (PDF or photo)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Read a health document and store the markers, pending confirmation.

    The uploaded file is parsed in memory and discarded when this request
    ends — it is never written to disk. Nothing extracted here influences any
    product score until the user confirms it.
    """
    _require_encryption()
    await _require_consent(db, current_user.id)

    if file.content_type and file.content_type.lower() not in ACCEPTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{file.content_type}' files aren't supported. Upload a PDF or a photo.",
        )

    # The limit is applied while reading rather than after, so an oversized
    # document costs the limit in memory and not whatever was sent.
    data = await read_upload_capped(
        file,
        settings.MAX_HEALTH_DOCUMENT_MB * 1024 * 1024,
        too_large_detail=(
            f"That file is larger than the {settings.MAX_HEALTH_DOCUMENT_MB}MB limit."
        ),
    )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That file is empty.",
        )

    from app.health.extraction import extract_from_document

    result = await extract_from_document(data, file.content_type, file.filename)
    # `data` goes out of scope here and is never persisted.

    markers = [m.to_dict() for m in result.markers]
    title = (file.filename or "Health document").rsplit("/", 1)[-1][:255]

    document = HealthDocument(
        user_id=current_user.id,
        title=title,
        document_type="blood_test",
        markers_encrypted=encrypt_json(markers),
        marker_count=len(markers),
        extraction_source=result.source,
        extraction_model=result.model,
        confirmed_at=None,
    )
    db.add(document)
    await db.flush()

    return HealthExtractionResponse(
        document=_to_detail(document, markers, result.warnings),
        warnings=result.warnings,
    )


@router.get("/documents/{document_id}", response_model=HealthDocumentDetail)
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One document's markers. Scoped to the owner."""
    _require_encryption()
    document = await _owned_document(db, document_id, current_user.id)
    return _to_detail(document, _load_markers(document))


@router.put("/documents/{document_id}", response_model=HealthDocumentDetail)
async def confirm_document(
    document_id: int,
    data: HealthConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save the markers as the user corrected them, and mark the document
    reviewed.

    Marking it reviewed is what lets these readings reach the scoring engine,
    so it happens only through this endpoint — never as a side effect of
    upload.
    """
    _require_encryption()
    await _require_consent(db, current_user.id)
    document = await _owned_document(db, document_id, current_user.id)

    if len(data.markers) > settings.MAX_HEALTH_MARKERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"A document can hold at most {settings.MAX_HEALTH_MARKERS} readings.",
        )

    markers = [_rescore_marker(m) for m in data.markers if (m.label or "").strip()]

    document.markers_encrypted = encrypt_json(markers)
    document.marker_count = len(markers)
    document.confirmed_at = datetime.now(timezone.utc)
    if data.title:
        document.title = data.title[:255]

    await db.flush()
    return _to_detail(document, markers)


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a document and its markers. Irreversible, by design."""
    document = await _owned_document(db, document_id, current_user.id)
    await db.delete(document)
    await db.flush()


async def _owned_document(
    db: AsyncSession, document_id: int, user_id: int
) -> HealthDocument:
    """
    Fetch a document, or 404.

    Scoped by user_id in the same query as the id, so another user's document
    is indistinguishable from one that does not exist — a 403 here would
    confirm that a given document belongs to someone.
    """
    document = (await db.execute(
        select(HealthDocument).where(
            HealthDocument.id == document_id,
            HealthDocument.user_id == user_id,
        )
    )).scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found."
        )
    return document
