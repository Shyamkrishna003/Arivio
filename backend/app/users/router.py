"""
User profile API routes.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.db.session import get_db
from app.core.security import get_current_user
from app.users.models import (
    User, UserProfile, UserGoal, UserAllergy, UserPreference, UserScanHistory,
    PrivacySetting,
)
from app.products.models import SavedProduct
from app.users.schemas import (
    ProfileUpdate, ProfileResponse, GoalCreate, GoalResponse,
    AllergyCreate, AllergyResponse, PreferenceCreate, PreferenceResponse,
    FullProfileResponse, DashboardResponse, RecentActivityItem,
    PrivacyResponse, PrivacyUpdate,
)
from sqlalchemy import func
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/profile", tags=["User Profile"])


@router.get("", response_model=FullProfileResponse)
async def get_full_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the complete user profile including goals, preferences, and allergies."""
    # Eagerly load relationships
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()

    goals_result = await db.execute(
        select(UserGoal).where(UserGoal.user_id == current_user.id, UserGoal.is_active == True)
    )
    goals = goals_result.scalars().all()

    prefs_result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == current_user.id)
    )
    preferences = prefs_result.scalars().all()

    allergies_result = await db.execute(
        select(UserAllergy).where(UserAllergy.user_id == current_user.id)
    )
    allergies = allergies_result.scalars().all()

    return FullProfileResponse(
        user_id=current_user.id,
        profile=profile,
        goals=goals,
        preferences=preferences,
        allergies=allergies,
    )


@router.put("", response_model=ProfileResponse)
async def update_profile(
    data: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user profile (progressive profiling)."""
    result = await db.execute(
        select(UserProfile).where(UserProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()

    if not profile:
        profile = UserProfile(user_id=current_user.id)
        db.add(profile)

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(profile, field, value)

    await db.flush()
    return profile


@router.get("/options")
async def profile_options():
    """
    The values the profile fields accept.

    Served rather than duplicated in the client so the dropdowns cannot drift
    from what the API validates — and, in the case of age bands, from the
    ordered scale community relevance compares them on.
    """
    from app.users.models import AGE_RANGES, ActivityLevel, DietaryPattern
    from app.personalization.engine import PREFERENCE_PROFILES

    def label(value: str) -> str:
        return value.replace("_", " ").capitalize()

    return {
        "age_ranges": [{"value": v, "label": v.capitalize()} for v in AGE_RANGES],
        "activity_levels": [{"value": e.value, "label": label(e.value)} for e in ActivityLevel],
        "dietary_patterns": [{"value": e.value, "label": label(e.value)} for e in DietaryPattern],
        "preferences": [
            {"value": key, "label": profile["label"],
             "nutrient": profile["nutrient"], "direction": profile["direction"]}
            for key, profile in PREFERENCE_PROFILES.items()
        ],
    }


@router.post("/goals", response_model=GoalResponse, status_code=status.HTTP_201_CREATED)
async def add_goal(
    data: GoalCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a user goal."""
    from app.personalization.engine import GOAL_PROFILES, _normalize, _resolve_goal_key
    from app.users.models import CustomGoalProfile, GoalProfileStatus

    payload = data.model_dump()
    # Store the goal type in a canonical, normalized form so it matches both the
    # hardcoded GOAL_PROFILES keys and any AI-generated CustomGoalProfile rows.
    normalized = _normalize(payload["goal_type"])
    payload["goal_type"] = normalized

    # Reject a goal that resolves to the same scoring profile as one the user
    # already has active — whether it's literally identical text or just an
    # alias of it (e.g. "weight loss" and "weight management" both resolve to
    # the "weight loss" profile). The composite score averages goals by
    # weight, so two rows scored against the same profile would silently
    # double that goal's influence instead of being rejected as a duplicate.
    resolved_key = _resolve_goal_key(normalized)
    existing_goals = await db.execute(
        select(UserGoal).where(UserGoal.user_id == current_user.id, UserGoal.is_active == True)
    )
    for existing_goal in existing_goals.scalars().all():
        if _resolve_goal_key(existing_goal.goal_type) == resolved_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You already have an active goal for '{existing_goal.goal_type}'.",
            )

    # Resolve the status up front where we can, so a goal we already support is
    # never shown as "pending" for the length of a pointless background task.
    if _resolve_goal_key(normalized) in GOAL_PROFILES:
        status_value, message, needs_generation = GoalProfileStatus.READY.value, None, False
    else:
        existing = await db.execute(
            select(CustomGoalProfile).where(CustomGoalProfile.goal_type == normalized)
        )
        if existing.scalar_one_or_none():
            status_value, message, needs_generation = GoalProfileStatus.READY.value, None, False
        else:
            status_value = GoalProfileStatus.PENDING.value
            message = "We're checking this goal and building a scoring profile for it."
            needs_generation = True

    goal = UserGoal(
        user_id=current_user.id,
        profile_status=status_value,
        status_message=message,
        **payload,
    )
    db.add(goal)
    await db.flush()

    if needs_generation:
        from app.users.tasks import generate_custom_goal_profile_ai
        background_tasks.add_task(generate_custom_goal_profile_ai, normalized)

    return goal


@router.delete("/goals/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_goal(
    goal_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a user goal."""
    result = await db.execute(
        select(UserGoal).where(UserGoal.id == goal_id, UserGoal.user_id == current_user.id)
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    await db.delete(goal)


@router.get("/allergens/known", response_model=List[str])
async def list_known_allergens():
    """
    Canonical allergen names the engine has synonym coverage for.

    Used to drive autocomplete so users pick "sesame" rather than typing
    "sesame seeds", which only resolves via the weaker token fallback.
    """
    from app.personalization.engine import known_allergen_suggestions
    return known_allergen_suggestions()


@router.post("/allergies", response_model=AllergyResponse, status_code=status.HTTP_201_CREATED)
async def add_allergy(
    data: AllergyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an allergy/intolerance entry."""
    from app.personalization.engine import _normalize

    # Reject an exact repeat (same allergen text, same type) — the engine
    # loops over every declared allergy and appends a flag per match, so a
    # duplicate would just render the same warning/danger flag twice.
    normalized_new = _normalize(data.allergen)
    existing_allergies = await db.execute(
        select(UserAllergy).where(UserAllergy.user_id == current_user.id)
    )
    for existing_allergy in existing_allergies.scalars().all():
        if (
            _normalize(existing_allergy.allergen) == normalized_new
            and existing_allergy.allergy_type == data.allergy_type
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You already have '{existing_allergy.allergen}' listed as a {data.allergy_type.value}.",
            )

    allergy = UserAllergy(user_id=current_user.id, **data.model_dump())
    db.add(allergy)
    await db.flush()
    return allergy


@router.delete("/allergies/{allergy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_allergy(
    allergy_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an allergy entry."""
    result = await db.execute(
        select(UserAllergy).where(UserAllergy.id == allergy_id, UserAllergy.user_id == current_user.id)
    )
    allergy = result.scalar_one_or_none()
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy not found")
    await db.delete(allergy)


@router.post("/preferences", response_model=PreferenceResponse, status_code=status.HTTP_201_CREATED)
async def add_preference(
    data: PreferenceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a nutrient preference."""
    # One entry per nutrient: a repeat would be scored twice, doubling the
    # nudge it applies. Re-adding updates the strictness instead.
    existing = await db.execute(
        select(UserPreference).where(
            UserPreference.user_id == current_user.id,
            UserPreference.preference_type == data.preference_type,
        )
    )
    current = existing.scalar_one_or_none()
    if current is not None:
        current.is_hard_constraint = data.is_hard_constraint
        await db.flush()
        return current

    pref = UserPreference(user_id=current_user.id, **data.model_dump())
    db.add(pref)
    await db.flush()
    return pref


@router.delete("/preferences/{preference_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_preference(
    preference_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a dietary preference."""
    result = await db.execute(
        select(UserPreference).where(
            UserPreference.id == preference_id, UserPreference.user_id == current_user.id
        )
    )
    pref = result.scalar_one_or_none()
    if not pref:
        raise HTTPException(status_code=404, detail="Preference not found")
    await db.delete(pref)

@router.get("/privacy", response_model=PrivacyResponse)
async def get_privacy(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    What this account currently permits to be shared with a community review.

    Everything is off unless explicitly enabled — sharing is opt-in, and the
    row is created in that state at registration.
    """
    result = await db.execute(
        select(PrivacySetting).where(PrivacySetting.user_id == current_user.id)
    )
    settings_row = result.scalar_one_or_none()
    if not settings_row:
        # An older account registered before privacy defaults existed. Create
        # the row in its fully-private state rather than assuming consent.
        settings_row = PrivacySetting(user_id=current_user.id)
        db.add(settings_row)
        await db.flush()
    return settings_row


@router.put("/privacy", response_model=PrivacyResponse)
async def update_privacy(
    data: PrivacyUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Update anonymous context-sharing consent.

    Consent is not retroactive in one direction only: withdrawing it stops
    future sharing, and re-submitting an experience rebuilds its shared context
    from the settings in force at that moment. Context already attached to past
    reviews is removed when a review is resubmitted without consent — see
    `create_review`.
    """
    result = await db.execute(
        select(PrivacySetting).where(PrivacySetting.user_id == current_user.id)
    )
    settings_row = result.scalar_one_or_none()
    if not settings_row:
        settings_row = PrivacySetting(user_id=current_user.id)
        db.add(settings_row)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(settings_row, field, value)

    await db.flush()
    return settings_row


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get dashboard KPIs and recent activity."""
    # 1. Total Scans
    scans_count = await db.execute(select(func.count(UserScanHistory.id)).where(UserScanHistory.user_id == current_user.id))
    total_scans = scans_count.scalar() or 0

    # 2. Saved Products
    saved_count = await db.execute(select(func.count(SavedProduct.id)).where(SavedProduct.user_id == current_user.id))
    saved_products = saved_count.scalar() or 0

    # 3. Active Goals
    goals_count = await db.execute(select(func.count(UserGoal.id)).where(UserGoal.user_id == current_user.id, UserGoal.is_active == True))
    active_goals = goals_count.scalar() or 0

    # 4. Recent Activity
    activity_query = await db.execute(
        select(UserScanHistory)
        .options(joinedload(UserScanHistory.product))
        .where(UserScanHistory.user_id == current_user.id)
        .order_by(UserScanHistory.scanned_at.desc())
        .limit(10)
    )
    activity_records = activity_query.scalars().all()
    
    recent_activity = []
    for record in activity_records:
        if record.product:
            recent_activity.append(RecentActivityItem(
                id=record.id,
                product_id=record.product.id,
                product_name=record.product.name,
                product_brand=record.product.brand,
                product_image_url=record.product.image_url,
                scanned_at=record.scanned_at
            ))

    return DashboardResponse(
        total_scans=total_scans,
        saved_products=saved_products,
        active_goals=active_goals,
        recent_activity=recent_activity
    )

@router.post("/history/{product_id}", status_code=status.HTTP_201_CREATED)
async def add_to_history(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Record that a user scanned or viewed a product.

    Written as a single upsert rather than read-then-write. The previous
    version selected the row, then inserted if it was absent — and two
    concurrent calls both saw it absent and both inserted, putting the product
    in Recent Activity twice. That is not hypothetical: React StrictMode
    double-invokes the effect that calls this, so it happened on essentially
    every scan in development.

    An application-level check cannot close that race whatever order it runs
    in; only the database can, via the unique constraint this conflicts on.
    """
    from datetime import datetime, timezone

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(UserScanHistory)
        .values(
            user_id=current_user.id,
            product_id=product_id,
            scanned_at=datetime.now(timezone.utc),
        )
        # Seeing a product again updates when, it does not add a second row.
        .on_conflict_do_update(
            constraint="uq_scan_history_user_product",
            set_={"scanned_at": datetime.now(timezone.utc)},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return {"status": "success"}
