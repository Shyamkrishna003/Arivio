"""
Personalization API routes.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.core.security import get_current_user
from app.users.models import User, UserGoal, UserAllergy, UserPreference, CustomGoalProfile
from app.products.models import (
    Product, NutritionFact, ProductAllergen, ProductIngredient
)
from app.personalization.engine import (
    calculate_suitability, _normalize, find_unresolved_allergens
)
from app.allergens.inference import resolve_allergens
from app.personalization.schemas import SuitabilityResponse, AlternativeProduct, AlternativesResponse

router = APIRouter(prefix="/personalization", tags=["Personalization"])


@router.get("/suitability/{product_id}", response_model=SuitabilityResponse)
async def get_suitability(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate the Personal Suitability Score for a product,
    personalized to the currently authenticated user.
    """
    # ── Load the product ──
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
                "weights": cp.weights or {}
            }

    # ── Load user allergies ──
    allergies_result = await db.execute(
        select(UserAllergy).where(UserAllergy.user_id == current_user.id)
    )
    user_allergies = [
        {"allergen": a.allergen, "allergy_type": a.allergy_type.value, "severity": a.severity}
        for a in allergies_result.scalars().all()
    ]

    # ── Load user preferences ──
    prefs_result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == current_user.id)
    )
    user_preferences = [
        {"preference_type": p.preference_type, "is_hard_constraint": p.is_hard_constraint}
        for p in prefs_result.scalars().all()
    ]

    # ── AI fallback for allergens the synonym tables don't cover ──
    # Only for allergens with no known synonyms AND no literal hit, where a
    # clean result would otherwise prove nothing. Cached per (allergen,
    # product), so this costs a model call once per pair.
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

    # ── Run the engine ──
    try:
        result = calculate_suitability(
            product_nutrition=product_nutrition,
            product_allergens=product_allergens,
            product_ingredients=product_ingredients,
            user_goals=user_goals,
            user_allergies=user_allergies,
            user_preferences=user_preferences,
            custom_profiles=custom_profiles,
            inferred_allergen_matches=inferred_allergen_matches,
        )
    except Exception as e:
        import traceback
        trace = traceback.format_exc()
        print(trace)
        raise HTTPException(status_code=500, detail=f"Error in suitability calculation: {str(e)}\n{trace}")

    return SuitabilityResponse(
        overall_score=result.overall_score,
        verdict=result.verdict,
        confidence=result.confidence,
        allergen_safe=result.allergen_safe,
        flags=[
            {
                "flag_type": f.flag_type,
                "category": f.category,
                "title": f.title,
                "description": f.description,
                "impact": f.impact,
            }
            for f in result.flags
        ],
        goal_alignments=[
            {
                "goal": ga.goal,
                "alignment": ga.alignment,
                "score": ga.score,
                "reason": ga.reason,
                "evaluated": ga.matched_profile,
            }
            for ga in result.goal_alignments
        ],
        nutritional_quality_score=result.nutritional_quality_score,
        ingredient_profile_score=result.ingredient_profile_score,
        breakdown=result.breakdown,
    )

@router.get("/alternatives/{product_id}", response_model=AlternativesResponse)
async def get_alternatives(
    product_id: int,
    limit: int = 5,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Find highly suitable alternative products in the same category.
    """
    from app.products.schemas import ProductResponse
    # ── Load original product ──
    result = await db.execute(select(Product).where(Product.id == product_id))
    original_product = result.scalar_one_or_none()
    if not original_product:
        raise HTTPException(status_code=404, detail="Product not found")

    if not original_product.category:
        # If no category, we can't easily find alternatives.
        return AlternativesResponse(
            original_product_id=product_id,
            category="Unknown",
            alternatives=[]
        )

    # ── Load user profile ──
    goals_result = await db.execute(
        select(UserGoal).where(UserGoal.user_id == current_user.id, UserGoal.is_active == True)
    )
    user_goals = [{"goal_type": g.goal_type, "priority": g.priority,
                   "profile_status": g.profile_status, "status_message": g.status_message}
                  for g in goals_result.scalars().all()]
    
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
                "weights": cp.weights or {}
            }

    allergies_result = await db.execute(select(UserAllergy).where(UserAllergy.user_id == current_user.id))
    user_allergies = [{"allergen": a.allergen, "allergy_type": a.allergy_type.value, "severity": a.severity} for a in allergies_result.scalars().all()]

    prefs_result = await db.execute(select(UserPreference).where(UserPreference.user_id == current_user.id))
    user_preferences = [{"preference_type": p.preference_type, "is_hard_constraint": p.is_hard_constraint} for p in prefs_result.scalars().all()]

    # ── Score the original product itself ──
    # "Alternatives" only means something relative to what the user is already
    # looking at — without this, a flat score threshold could surface a
    # candidate that actually scores *worse* than the original product while
    # the UI claims it "scored higher".
    orig_nutrition_result = await db.execute(
        select(NutritionFact).where(NutritionFact.product_id == product_id)
    )
    orig_nutrition_row = orig_nutrition_result.scalar_one_or_none()
    original_nutrition = None
    if orig_nutrition_row:
        original_nutrition = {
            "energy_kcal": orig_nutrition_row.energy_kcal,
            "protein_g": orig_nutrition_row.protein_g,
            "total_fat_g": orig_nutrition_row.total_fat_g,
            "saturated_fat_g": orig_nutrition_row.saturated_fat_g,
            "trans_fat_g": orig_nutrition_row.trans_fat_g,
            "total_carbohydrates_g": orig_nutrition_row.total_carbohydrates_g,
            "total_sugars_g": orig_nutrition_row.total_sugars_g,
            "fiber_g": orig_nutrition_row.dietary_fiber_g,
            "sodium_mg": orig_nutrition_row.sodium_mg,
            "cholesterol_mg": orig_nutrition_row.cholesterol_mg,
        }

    orig_allergens_result = await db.execute(
        select(ProductAllergen).where(ProductAllergen.product_id == product_id)
    )
    original_allergens = [
        {"allergen": a.allergen, "certainty": a.certainty}
        for a in orig_allergens_result.scalars().all()
    ]

    orig_ingredients_result = await db.execute(
        select(ProductIngredient)
        .where(ProductIngredient.product_id == product_id)
        .order_by(ProductIngredient.position)
    )
    original_ingredients = [
        {"name": i.name, "position": i.position, "percentage": i.percentage}
        for i in orig_ingredients_result.scalars().all()
    ]

    try:
        orig_unresolved = find_unresolved_allergens(
            user_allergies, original_allergens, original_ingredients
        )
        orig_inferred = await resolve_allergens(
            db=db,
            product_id=product_id,
            product_name=original_product.name,
            unresolved=orig_unresolved,
            product_ingredients=original_ingredients,
            product_allergens=original_allergens,
            allow_ai=False,
        ) if orig_unresolved else {}
    except Exception:
        orig_inferred = {}

    try:
        original_suitability = calculate_suitability(
            product_nutrition=original_nutrition,
            product_allergens=original_allergens,
            product_ingredients=original_ingredients,
            user_goals=user_goals,
            user_allergies=user_allergies,
            user_preferences=user_preferences,
            custom_profiles=custom_profiles,
            inferred_allergen_matches=orig_inferred,
        )
        original_score = original_suitability.overall_score
    except Exception:
        # If we can't score the original product, fall back to the plain
        # quality floor below rather than blocking alternatives entirely.
        original_score = 0

    # ── Find candidates in same category ──
    candidates_result = await db.execute(
        select(Product)
        .where(Product.category == original_product.category, Product.id != product_id)
        .limit(50)
    )
    candidates = candidates_result.scalars().all()

    if not candidates:
        return AlternativesResponse(
            original_product_id=product_id,
            category=original_product.category,
            alternatives=[]
        )

    # Fetch all nutrition for candidates
    candidate_ids = [c.id for c in candidates]
    nutrition_result = await db.execute(select(NutritionFact).where(NutritionFact.product_id.in_(candidate_ids)))
    nutrition_rows = nutrition_result.scalars().all()
    nutrition_map = {n.product_id: n for n in nutrition_rows}

    # Fetch all allergens
    allergens_result = await db.execute(select(ProductAllergen).where(ProductAllergen.product_id.in_(candidate_ids)))
    allergens_rows = allergens_result.scalars().all()
    allergens_map = {}
    for a in allergens_rows:
        if a.product_id not in allergens_map:
            allergens_map[a.product_id] = []
        allergens_map[a.product_id].append({"allergen": a.allergen, "certainty": a.certainty})

    # Fetch all ingredients
    ingredients_result = await db.execute(select(ProductIngredient).where(ProductIngredient.product_id.in_(candidate_ids)))
    ingredients_rows = ingredients_result.scalars().all()
    ingredients_map = {}
    for i in ingredients_rows:
        if i.product_id not in ingredients_map:
            ingredients_map[i.product_id] = []
        ingredients_map[i.product_id].append({"name": i.name, "position": i.position, "percentage": i.percentage})

    alternatives = []
    
    for product in candidates:
        # Build product data
        n_row = nutrition_map.get(product.id)
        product_nutrition = None
        if n_row:
            product_nutrition = {
                "energy_kcal": n_row.energy_kcal,
                "protein_g": n_row.protein_g,
                "total_fat_g": n_row.total_fat_g,
                "saturated_fat_g": n_row.saturated_fat_g,
                "trans_fat_g": n_row.trans_fat_g,
                "total_carbohydrates_g": n_row.total_carbohydrates_g,
                "total_sugars_g": n_row.total_sugars_g,
                "fiber_g": n_row.dietary_fiber_g,
                "sodium_mg": n_row.sodium_mg,
                "cholesterol_mg": n_row.cholesterol_mg,
            }
        
        product_allergens = allergens_map.get(product.id, [])
        product_ingredients = ingredients_map.get(product.id, [])
        product_ingredients.sort(key=lambda x: x["position"])

        # Cached allergen verdicts only. This loop scores up to 50 candidates,
        # so firing a model call per candidate would be prohibitively slow —
        # a product already viewed will have its verdict cached.
        try:
            unresolved = find_unresolved_allergens(
                user_allergies, product_allergens, product_ingredients
            )
            inferred = await resolve_allergens(
                db=db,
                product_id=product.id,
                product_name=product.name,
                unresolved=unresolved,
                product_ingredients=product_ingredients,
                product_allergens=product_allergens,
                allow_ai=False,
            ) if unresolved else {}
        except Exception:
            inferred = {}

        # Calculate suitability
        try:
            suitability = calculate_suitability(
                product_nutrition=product_nutrition,
                product_allergens=product_allergens,
                product_ingredients=product_ingredients,
                user_goals=user_goals,
                user_allergies=user_allergies,
                user_preferences=user_preferences,
                custom_profiles=custom_profiles,
                inferred_allergen_matches=inferred,
            )
        except Exception:
            continue
            
        # Only suggest if it's safe, meets a reasonable quality floor, and
        # actually scores higher than the product the user is looking at —
        # matching the "these products scored higher" claim shown in the UI.
        if (
            suitability.allergen_safe
            and suitability.overall_score >= 70
            and suitability.overall_score > original_score
        ):
            # Gather match reasons from goal alignments
            match_reasons = []
            for ga in suitability.goal_alignments:
                if ga.alignment in ["excellent", "good"]:
                    match_reasons.append(f"{ga.goal.title()}: {ga.reason}")
            
            # Fallback if no goal alignments were good but score is high
            if not match_reasons:
                if suitability.nutritional_quality_score >= 80:
                    match_reasons.append("High overall nutritional quality.")
                else:
                    match_reasons.append("Generally suitable for your profile.")

            alternatives.append({
                "product_obj": product,
                "suitability_score": suitability.overall_score,
                "verdict": suitability.verdict,
                "match_reasons": match_reasons[:2] # Top 2 reasons
            })

    # Sort descending by score
    alternatives.sort(key=lambda x: x["suitability_score"], reverse=True)
    top_alternatives = alternatives[:limit]

    # Map to schema
    result_list = []
    for alt in top_alternatives:
        prod_resp = ProductResponse.model_validate(alt["product_obj"])
        result_list.append(
            AlternativeProduct(
                product=prod_resp,
                suitability_score=alt["suitability_score"],
                verdict=alt["verdict"],
                match_reasons=alt["match_reasons"]
            )
        )

    return AlternativesResponse(
        original_product_id=product_id,
        category=original_product.category,
        alternatives=result_list
    )
