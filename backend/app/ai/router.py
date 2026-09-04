"""
AI Report API routes.

Provides endpoints for:
1. Generating AI-powered product reports
2. Submitting and retrieving report feedback
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.core.security import get_current_user
from app.users.models import User, UserGoal, UserAllergy, CustomGoalProfile
from app.products.models import (
    Product, NutritionFact, ProductAllergen, ProductIngredient
)
from app.personalization.engine import (
    calculate_suitability, _normalize, find_unresolved_allergens
)
from app.allergens.inference import resolve_allergens
from app.ai.gateway import generate_report
from app.ai.schemas import AIReportResponse, ReportFeedbackCreate, ReportFeedbackResponse
from app.ai.models import ReportFeedback

router = APIRouter(prefix="/ai", tags=["AI Reports"])


@router.get("/report/{product_id}", response_model=AIReportResponse)
async def get_ai_report(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate an AI-powered personalized product intelligence report.

    This endpoint:
    1. Loads product data (nutrition, ingredients, allergens)
    2. Loads the user's health profile (goals, allergies)
    3. Runs the suitability engine to get scores
    4. Feeds everything into the AI gateway for natural-language analysis
    """

    # ── Load product ──
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # ── Load product nutrition ──
    nutrition_result = await db.execute(
        select(NutritionFact).where(NutritionFact.product_id == product_id)
    )
    nutrition_row = nutrition_result.scalar_one_or_none()

    product_nutrition = None
    if nutrition_row:
        product_nutrition = {
            "energy_kcal": nutrition_row.energy_kcal,
            "protein_g": nutrition_row.protein_g,
            "total_fat_g": nutrition_row.total_fat_g,
            "saturated_fat_g": nutrition_row.saturated_fat_g,
            "trans_fat_g": nutrition_row.trans_fat_g,
            "total_carbohydrates_g": nutrition_row.total_carbohydrates_g,
            "total_sugars_g": nutrition_row.total_sugars_g,
            "fiber_g": nutrition_row.dietary_fiber_g,
            "sodium_mg": nutrition_row.sodium_mg,
            "cholesterol_mg": nutrition_row.cholesterol_mg,
        }

    # ── Load product allergens ──
    allergens_result = await db.execute(
        select(ProductAllergen).where(ProductAllergen.product_id == product_id)
    )
    product_allergens = [
        {"allergen": a.allergen, "certainty": a.certainty}
        for a in allergens_result.scalars().all()
    ]

    # ── Load product ingredients ──
    ingredients_result = await db.execute(
        select(ProductIngredient)
        .where(ProductIngredient.product_id == product_id)
        .order_by(ProductIngredient.position)
    )
    product_ingredients = [
        {"name": i.name, "position": i.position, "percentage": i.percentage}
        for i in ingredients_result.scalars().all()
    ]

    # ── Load user goals ──
    goals_result = await db.execute(
        select(UserGoal)
        .where(UserGoal.user_id == current_user.id, UserGoal.is_active == True)
    )
    user_goals = [
        {"goal_type": g.goal_type, "priority": g.priority,
         "profile_status": g.profile_status, "status_message": g.status_message}
        for g in goals_result.scalars().all()
    ]

    # ── Load custom goal profiles for the active goals ──
    custom_profiles = {}
    if user_goals:
        active_goal_types = list({_normalize(g["goal_type"]) for g in user_goals})
        custom_profiles_result = await db.execute(
            select(CustomGoalProfile).where(CustomGoalProfile.goal_type.in_(active_goal_types))
        )
        for cp in custom_profiles_result.scalars().all():
            custom_profiles[cp.goal_type] = {
                "label": cp.label,
                "prefer_low": cp.prefer_low,
                "prefer_high": cp.prefer_high,
                "thresholds": cp.thresholds,
                "weights": cp.weights or {},
            }

    # ── Load user allergies ──
    allergies_result = await db.execute(
        select(UserAllergy).where(UserAllergy.user_id == current_user.id)
    )
    user_allergies = [
        {"allergen": a.allergen, "allergy_type": a.allergy_type.value, "severity": a.severity}
        for a in allergies_result.scalars().all()
    ]

    # ── AI fallback for allergens the synonym tables don't cover ──
    inferred_allergen_matches = {}
    unresolved = find_unresolved_allergens(
        user_allergies, product_allergens, product_ingredients
    )
    if unresolved:
        inferred_allergen_matches = await resolve_allergens(
            db=db,
            product_id=product_id,
            product_name=product.name,
            unresolved=unresolved,
            product_ingredients=product_ingredients,
            product_allergens=product_allergens,
        )

    # ── Run suitability engine (now with custom_profiles) ──
    suitability = calculate_suitability(
        product_nutrition=product_nutrition,
        product_allergens=product_allergens,
        product_ingredients=product_ingredients,
        user_goals=user_goals,
        user_allergies=user_allergies,
        user_preferences=[],
        custom_profiles=custom_profiles,
        inferred_allergen_matches=inferred_allergen_matches,
    )

    # Convert flags and goal_alignments to dicts for the gateway
    flags_dicts = [
        {"flag_type": f.flag_type, "category": f.category, "title": f.title,
         "description": f.description, "impact": f.impact}
        for f in suitability.flags
    ]
    ga_dicts = [
        {"goal": ga.goal, "alignment": ga.alignment, "score": ga.score,
         "reason": ga.reason, "evaluated": ga.matched_profile}
        for ga in suitability.goal_alignments
    ]

    # ── Fetch Past Feedback for Few-Shot Learning ──
    # Include ALL recent feedback, not just the ones carrying a written comment —
    # a low star rating with no comment is still a signal that the previous
    # report missed the mark. Negative feedback is the most actionable.
    feedback_result = await db.execute(
        select(ReportFeedback)
        .where(ReportFeedback.user_id == current_user.id)
        .order_by(ReportFeedback.created_at.desc())
        .limit(8)
    )
    user_feedback_examples = [
        {
            "rating": fb.rating,
            "comment": fb.comment or "",
            "feedback_type": fb.feedback_type,
        }
        for fb in feedback_result.scalars().all()
    ]

    # ── Generate AI report ──
    try:
        report = await generate_report(
            product_name=product.name,
            product_brand=product.brand,
            product_category=product.category,
            suitability_score=suitability.overall_score,
            verdict=suitability.verdict,
            allergen_safe=suitability.allergen_safe,
            flags=flags_dicts,
            goal_alignments=ga_dicts,
            nutrition=product_nutrition,
            ingredients=product_ingredients,
            user_goals=user_goals,
            user_allergies=user_allergies,
            user_feedback_examples=user_feedback_examples if user_feedback_examples else None,
        )
    except Exception as e:
        import traceback
        trace = traceback.format_exc()
        print(f"AI Report generation failed: {trace}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate AI report: {str(e)}"
        )

    return AIReportResponse(
        summary=report.summary,
        detailed_analysis=report.detailed_analysis,
        key_insights=report.key_insights,
        recommendations=report.recommendations,
        confidence_note=report.confidence_note,
        provider=report.provider,
        model=report.model,
    )


@router.post("/feedback", response_model=ReportFeedbackResponse)
async def submit_feedback(
    feedback: ReportFeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit user feedback on an AI-generated report.
    Helps improve report quality over time.
    """
    # Validate rating
    if feedback.rating < 1 or feedback.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    # Validate product exists
    result = await db.execute(select(Product).where(Product.id == feedback.product_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Product not found")

    # One rating per user per product: re-rating updates the existing row.
    # Inserting unconditionally piled up duplicates, which then broke the
    # read side (it expects at most one row per product).
    existing_result = await db.execute(
        select(ReportFeedback)
        .where(
            ReportFeedback.user_id == current_user.id,
            ReportFeedback.product_id == feedback.product_id,
        )
        .order_by(ReportFeedback.created_at.desc())
        .limit(1)
    )
    db_feedback = existing_result.scalars().first()

    if db_feedback:
        db_feedback.rating = feedback.rating
        db_feedback.feedback_type = feedback.feedback_type
        db_feedback.comment = feedback.comment
    else:
        db_feedback = ReportFeedback(
            user_id=current_user.id,
            product_id=feedback.product_id,
            rating=feedback.rating,
            feedback_type=feedback.feedback_type,
            comment=feedback.comment,
        )
        db.add(db_feedback)

    await db.commit()
    await db.refresh(db_feedback)

    return db_feedback


@router.get("/feedback/{product_id}")
async def get_feedback(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the current user's feedback for a specific product report."""
    # Take the most recent row rather than asserting there is exactly one:
    # historical data can hold more than one rating per product, and
    # scalar_one_or_none() turns that into a 500 the caller can't recover from.
    result = await db.execute(
        select(ReportFeedback)
        .where(
            ReportFeedback.user_id == current_user.id,
            ReportFeedback.product_id == product_id,
        )
        .order_by(ReportFeedback.created_at.desc())
        .limit(1)
    )
    feedback = result.scalars().first()
    if not feedback:
        return {"feedback": None}
    return {
        "feedback": {
            "id": feedback.id,
            "rating": feedback.rating,
            "feedback_type": feedback.feedback_type,
            "comment": feedback.comment,
            "created_at": feedback.created_at.isoformat(),
        }
    }
