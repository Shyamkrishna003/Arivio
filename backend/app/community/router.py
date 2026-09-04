"""
Community API routes.

Three ideas from the requirements drive the shape of this module:

  - Community data starts empty and grows only from real contributions.
    Nothing here seeds, simulates or backfills experiences, and an empty
    product reports that honestly rather than implying a consensus.

  - Overall and personalized aggregates are distinct answers. A reader must be
    able to tell "what everyone reported" from "what people like me reported".

  - An experience is never evidence of causation. Every aggregate carries that
    statement, and relevance is always explained rather than asserted.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.community.models import (
    CommunityReview, ReviewContext, ReviewVote, ReviewFlag, ExperienceType,
)
from app.community.moderation import screen_review, FLAG_THRESHOLD
from app.community.schemas import (
    ReviewCreate, ReviewResponse, ReviewListResponse, ReviewContextResponse,
    CommunitySummary, ExperienceBreakdownItem, RelevanceResponse,
    VoteCreate, FlagCreate,
)
from app.community.similarity import score_relevance
from app.core.security import get_current_user, get_optional_user
from app.db.session import get_db
from app.products.models import Product
from app.users.models import (
    User, UserProfile, UserGoal, UserAllergy, PrivacySetting,
)

router = APIRouter(prefix="/community", tags=["Community"])

# Below this many experiences, percentages misrepresent the data — "100%
# reported headaches" off a single review reads as a finding rather than one
# person's account. Counts are still reported; the caller is told not to draw
# proportions from them.
MIN_FOR_PERCENTAGES = 5

_EXPERIENCE_LABELS = {
    ExperienceType.NO_NOTICEABLE_EFFECT: "No noticeable effect",
    ExperienceType.POSITIVE: "Positive",
    ExperienceType.NEGATIVE: "Negative",
    ExperienceType.DIGESTIVE_DISCOMFORT: "Digestive discomfort",
    ExperienceType.SKIN_REACTION: "Skin reaction",
    ExperienceType.HEADACHE: "Headache",
    ExperienceType.ENERGY_CHANGE: "Energy change",
    ExperienceType.APPETITE_CHANGE: "Appetite change",
    ExperienceType.OTHER: "Other",
}


def _label(value) -> str:
    """Human-readable name for an experience type."""
    try:
        return _EXPERIENCE_LABELS[ExperienceType(value)]
    except (ValueError, KeyError):
        return str(value).replace("_", " ").capitalize()


async def _load_reader_profile(db: AsyncSession, user: User) -> dict:
    """
    The reader's own context, used only to judge which experiences are relevant.

    This never leaves the server. It is compared against the anonymised context
    other users consented to share; the reader's profile is not disclosed to
    anyone by taking part.
    """
    profile = (await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )).scalar_one_or_none()

    goals = [
        g.goal_type for g in (await db.execute(
            select(UserGoal).where(UserGoal.user_id == user.id, UserGoal.is_active == True)
        )).scalars().all()
    ]
    allergies = [
        a.allergen for a in (await db.execute(
            select(UserAllergy).where(UserAllergy.user_id == user.id)
        )).scalars().all()
    ]

    return {
        "age_range": profile.age_range if profile else None,
        "dietary_pattern": profile.dietary_pattern.value if profile and profile.dietary_pattern else None,
        "activity_level": profile.activity_level.value if profile and profile.activity_level else None,
        "goals": goals,
        "allergies": allergies,
        # Filled in by the caller from the reader's own review, when they have
        # one — comparing length of use needs both sides.
        "usage_duration": None,
    }


async def _build_shared_context(
    db: AsyncSession, user: User, usage_duration: str,
) -> tuple[dict, bool]:
    """
    Assemble the anonymised context a review may carry.

    Every attribute is gated twice: the account's privacy settings, and the
    per-review opt-in. A review can never share more than the profile permits,
    and the master switch withholds everything. Health context is never
    included — the requirement forbids exposing it through community profiles.
    """
    privacy = (await db.execute(
        select(PrivacySetting).where(PrivacySetting.user_id == user.id)
    )).scalar_one_or_none()

    if not privacy or not privacy.allow_anonymous_context_sharing:
        return {}, False

    profile = (await db.execute(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )).scalar_one_or_none()

    context: dict = {
        # Part of the experience itself rather than private profile data, so it
        # travels with any shared context.
        "usage_duration": usage_duration,
    }

    if privacy.share_age_range and profile and profile.age_range:
        context["age_range"] = profile.age_range
    if privacy.share_dietary_pattern and profile and profile.dietary_pattern:
        context["dietary_pattern"] = profile.dietary_pattern.value
    if privacy.share_activity_level and profile and profile.activity_level:
        context["activity_level"] = profile.activity_level.value
    if privacy.share_goals:
        goals = [
            g.goal_type for g in (await db.execute(
                select(UserGoal).where(UserGoal.user_id == user.id, UserGoal.is_active == True)
            )).scalars().all()
        ]
        if goals:
            context["goals"] = goals
    if privacy.share_allergies:
        allergies = [
            a.allergen for a in (await db.execute(
                select(UserAllergy).where(UserAllergy.user_id == user.id)
            )).scalars().all()
        ]
        if allergies:
            context["relevant_allergies"] = allergies

    # Usage duration alone tells a reader almost nothing about whether an
    # experience applies to them, so it is not worth storing on its own.
    return (context, len(context) > 1)


def _breakdown(reviews: list[CommunityReview]) -> list[ExperienceBreakdownItem]:
    """Count experiences by type, largest first."""
    total = len(reviews)
    if not total:
        return []
    counts: dict[str, int] = {}
    for r in reviews:
        key = r.experience_type.value if hasattr(r.experience_type, "value") else str(r.experience_type)
        counts[key] = counts.get(key, 0) + 1
    return [
        ExperienceBreakdownItem(
            experience_type=key,
            label=_label(key),
            count=count,
            percentage=round(count / total * 100, 1),
        )
        for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def _context_to_dict(ctx: ReviewContext | None) -> dict | None:
    if ctx is None:
        return None
    return {
        "age_range": ctx.age_range,
        "dietary_pattern": ctx.dietary_pattern,
        "activity_level": ctx.activity_level,
        "goals": ctx.goals,
        "relevant_allergies": ctx.relevant_allergies,
        "usage_duration": ctx.usage_duration,
    }


def _to_response(
    review: CommunityReview,
    *,
    is_mine: bool = False,
    my_vote: bool | None = None,
    relevance=None,
) -> ReviewResponse:
    ctx = _context_to_dict(review.anonymous_context)
    return ReviewResponse(
        id=review.id,
        product_id=review.product_id,
        usage_duration=review.usage_duration.value if hasattr(review.usage_duration, "value") else str(review.usage_duration),
        experience_type=review.experience_type.value if hasattr(review.experience_type, "value") else str(review.experience_type),
        experience_text=review.experience_text,
        rating=review.rating,
        helpful_count=review.helpful_count or 0,
        not_helpful_count=review.not_helpful_count or 0,
        created_at=review.created_at,
        is_mine=is_mine,
        is_published=bool(review.is_approved),
        # Only ever disclosed to the author, so a held review can explain itself
        # without publishing what tripped the check.
        moderation_note=review.flag_reason if (is_mine and not review.is_approved) else None,
        my_vote=my_vote,
        shared_context=ReviewContextResponse(**ctx) if ctx else None,
        relevance=relevance,
    )


@router.post("/reviews", response_model=ReviewResponse, status_code=status.HTTP_201_CREATED)
async def create_review(
    data: ReviewCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Share an experience with a product.

    One experience per user per product — a repeated voice would skew the
    aggregate. Re-submitting updates the existing experience instead.
    """
    product = (await db.execute(
        select(Product).where(Product.id == data.product_id)
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing = (await db.execute(
        select(CommunityReview)
        .options(selectinload(CommunityReview.anonymous_context))
        .where(
            CommunityReview.user_id == current_user.id,
            CommunityReview.product_id == data.product_id,
        )
    )).scalar_one_or_none()

    hold_reason = await screen_review(db, current_user.id, data.experience_text)

    review = existing or CommunityReview(
        user_id=current_user.id,
        product_id=data.product_id,
    )
    review.usage_duration = data.usage_duration
    review.experience_type = data.experience_type
    review.experience_text = data.experience_text
    review.rating = data.rating
    review.is_approved = hold_reason is None
    review.is_flagged = hold_reason is not None
    review.flag_reason = hold_reason

    if existing is None:
        db.add(review)
    await db.flush()

    # ── Anonymised context, rebuilt on every submission ──
    # The profile may have changed since the last one, and consent may have been
    # withdrawn — in which case the previously shared context is removed.
    context_values, worth_sharing = (
        await _build_shared_context(
            db, current_user,
            data.usage_duration.value if hasattr(data.usage_duration, "value") else str(data.usage_duration),
        )
        if data.share_context else ({}, False)
    )

    if existing is not None and existing.anonymous_context is not None:
        await db.delete(existing.anonymous_context)
        await db.flush()

    if worth_sharing:
        db.add(ReviewContext(review_id=review.id, **context_values))

    await db.flush()
    await db.refresh(review, attribute_names=["anonymous_context"])

    return _to_response(review, is_mine=True)


@router.get("/products/{product_id}", response_model=ReviewListResponse)
async def get_product_community(
    product_id: int,
    limit: int = 20,
    current_user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Community experiences for a product, overall and personalized.

    Works anonymously — the aggregate is public. The "people like you" split
    needs a profile to compare against, so it is empty for signed-out readers
    rather than being faked from popularity.
    """
    product = (await db.execute(
        select(Product).where(Product.id == product_id)
    )).scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    published = (await db.execute(
        select(CommunityReview)
        .options(selectinload(CommunityReview.anonymous_context))
        .where(
            CommunityReview.product_id == product_id,
            CommunityReview.is_approved == True,
        )
        .order_by(CommunityReview.created_at.desc())
    )).scalars().all()

    ratings = [r.rating for r in published if r.rating is not None]
    summary = CommunitySummary(
        total_experiences=len(published),
        breakdown=_breakdown(published),
        average_rating=round(sum(ratings) / len(ratings), 1) if ratings else None,
        insufficient_data=len(published) < MIN_FOR_PERCENTAGES,
    )

    my_review = None
    my_votes: dict[int, bool] = {}
    reader = None

    if current_user is not None:
        own = (await db.execute(
            select(CommunityReview)
            .options(selectinload(CommunityReview.anonymous_context))
            .where(
                CommunityReview.user_id == current_user.id,
                CommunityReview.product_id == product_id,
            )
        )).scalar_one_or_none()
        if own is not None:
            my_review = _to_response(own, is_mine=True)

        my_votes = {
            v.review_id: v.is_helpful
            for v in (await db.execute(
                select(ReviewVote).where(ReviewVote.user_id == current_user.id)
            )).scalars().all()
        }

        reader = await _load_reader_profile(db, current_user)
        if own is not None:
            reader["usage_duration"] = (
                own.usage_duration.value if hasattr(own.usage_duration, "value")
                else str(own.usage_duration)
            )

    relevant: list[tuple[float, ReviewResponse, CommunityReview]] = []
    others: list[ReviewResponse] = []

    for review in published:
        is_mine = current_user is not None and review.user_id == current_user.id
        vote = my_votes.get(review.id)

        relevance_payload = None
        matched = False
        # Your own experience is not offered back to you as a similar profile.
        if reader is not None and not is_mine:
            result = score_relevance(reader, _context_to_dict(review.anonymous_context))
            if result.is_relevant:
                matched = True
                relevance_payload = RelevanceResponse(
                    score=int(round(result.score * 100)),
                    reasons=result.reasons,
                    differences=result.differences,
                    not_compared=result.uncomparable,
                )

        response = _to_response(review, is_mine=is_mine, my_vote=vote, relevance=relevance_payload)
        if matched:
            relevant.append((relevance_payload.score, response, review))
        else:
            others.append(response)

    relevant.sort(key=lambda item: item[0], reverse=True)
    relevant_reviews = [r for _, r, _ in relevant][:limit]

    matched_models = [model for _, _, model in relevant]
    summary.relevant_experiences = len(matched_models)
    summary.relevant_breakdown = _breakdown(matched_models)

    return ReviewListResponse(
        product_id=product_id,
        summary=summary,
        relevant_reviews=relevant_reviews,
        recent_reviews=others[:limit],
        my_review=my_review,
    )


@router.post("/reviews/{review_id}/vote", response_model=ReviewResponse)
async def vote_review(
    review_id: int,
    data: VoteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an experience helpful or not. One vote per reader, changeable."""
    review = (await db.execute(
        select(CommunityReview)
        .options(selectinload(CommunityReview.anonymous_context))
        .where(CommunityReview.id == review_id)
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot vote on your own experience.",
        )

    existing = (await db.execute(
        select(ReviewVote).where(
            ReviewVote.user_id == current_user.id,
            ReviewVote.review_id == review_id,
        )
    )).scalar_one_or_none()

    if existing is None:
        db.add(ReviewVote(user_id=current_user.id, review_id=review_id, is_helpful=data.is_helpful))
    else:
        existing.is_helpful = data.is_helpful
    await db.flush()

    # Recount from the vote rows rather than incrementing. A counter that only
    # ever goes up cannot survive a changed vote, and drifts permanently once
    # it does.
    helpful = await db.execute(
        select(func.count(ReviewVote.id)).where(
            ReviewVote.review_id == review_id, ReviewVote.is_helpful == True)
    )
    not_helpful = await db.execute(
        select(func.count(ReviewVote.id)).where(
            ReviewVote.review_id == review_id, ReviewVote.is_helpful == False)
    )
    review.helpful_count = helpful.scalar() or 0
    review.not_helpful_count = not_helpful.scalar() or 0
    await db.flush()

    return _to_response(review, is_mine=False, my_vote=data.is_helpful)


@router.post("/reviews/{review_id}/flag", status_code=status.HTTP_202_ACCEPTED)
async def flag_review(
    review_id: int,
    data: FlagCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Report an experience for moderation.

    Reports accumulate. A single report does not remove anything — disagreeing
    with someone's experience is not grounds to hide it. At the threshold the
    review is withdrawn from the public aggregate pending review.
    """
    review = (await db.execute(
        select(CommunityReview).where(CommunityReview.id == review_id)
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot report your own experience.",
        )

    already = (await db.execute(
        select(ReviewFlag).where(
            ReviewFlag.user_id == current_user.id,
            ReviewFlag.review_id == review_id,
        )
    )).scalar_one_or_none()
    if already is None:
        db.add(ReviewFlag(user_id=current_user.id, review_id=review_id, reason=data.reason))
        await db.flush()

    count = (await db.execute(
        select(func.count(ReviewFlag.id)).where(ReviewFlag.review_id == review_id)
    )).scalar() or 0

    withdrawn = False
    if count >= FLAG_THRESHOLD and review.is_approved:
        review.is_approved = False
        review.is_flagged = True
        review.flag_reason = f"Withdrawn pending moderation after {count} reports."
        withdrawn = True
        await db.flush()

    return {
        "status": "reported",
        "reports": count,
        "withdrawn_pending_moderation": withdrawn,
    }


@router.delete("/reviews/{review_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_review(
    review_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Withdraw your own experience."""
    review = (await db.execute(
        select(CommunityReview).where(CommunityReview.id == review_id)
    )).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only remove your own experience.")
    await db.delete(review)
