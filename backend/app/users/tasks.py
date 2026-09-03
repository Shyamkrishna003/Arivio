import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.db.session import AsyncSessionLocal
from app.users.models import CustomGoalProfile, UserGoal, GoalProfileStatus
from app.ai.gateway import settings, _parse_ai_response
from app.personalization.engine import GOAL_PROFILES, _normalize, _resolve_goal_key
import traceback

# Valid nutrient keys that the scoring engine understands
VALID_NUTRIENT_KEYS = {
    "energy_kcal", "protein_g", "total_fat_g", "saturated_fat_g",
    "trans_fat_g", "total_carbohydrates_g", "total_sugars_g",
    "fiber_g", "sodium_mg", "cholesterol_mg"
}

# Shown when we cannot reach the AI at all — distinct from a goal the AI
# actively judged unscoreable, because this one is worth retrying.
_UNAVAILABLE_MSG = (
    "We couldn't build a scoring profile for this goal right now, so it isn't "
    "affecting your scores. Try removing and re-adding it later."
)


async def _set_goal_status(db: AsyncSession, normalized_goal: str, status: str, message: str | None):
    """
    Apply a status to every user's copy of this goal.

    CustomGoalProfile is global (goal_type is unique), so a verdict reached for
    one user applies to everyone who declared the same goal.
    """
    await db.execute(
        update(UserGoal)
        .where(UserGoal.goal_type == normalized_goal)
        .values(profile_status=status, status_message=message)
    )
    await db.commit()


async def generate_custom_goal_profile_ai(goal_type: str):
    """
    Background task to generate dynamic math thresholds for a custom user goal.

    Also acts as the sanity gate: the model may decline to profile a goal that
    is incoherent, unscoreable, or nutritionally harmful, in which case the goal
    is marked UNSUPPORTED with a user-facing reason instead of being silently
    given invented thresholds.
    """
    normalized_goal = _normalize(goal_type)

    # Check if it already resolves to a hardcoded profile — either directly or
    # through an alias ("muscle_building" -> "muscle gain"). Without the alias
    # check we would burn an LLM call regenerating a profile we already ship.
    if _resolve_goal_key(goal_type) in GOAL_PROFILES:
        async with AsyncSessionLocal() as db:
            await _set_goal_status(db, normalized_goal, GoalProfileStatus.READY.value, None)
        return

    if not settings.AI_API_KEY:
        async with AsyncSessionLocal() as db:
            await _set_goal_status(
                db, normalized_goal, GoalProfileStatus.UNSUPPORTED.value, _UNAVAILABLE_MSG
            )
        return

    try:
        async with AsyncSessionLocal() as db:
            # Check if it already exists in DB
            result = await db.execute(select(CustomGoalProfile).where(CustomGoalProfile.goal_type == normalized_goal))
            if result.scalar_one_or_none():
                await _set_goal_status(db, normalized_goal, GoalProfileStatus.READY.value, None)
                return

            # AI Prompt
            prompt = f"""
            You are an expert nutritionist and dietitian building a scoring engine.
            We need to evaluate food products per 100g serving for the goal: "{goal_type}".

            FIRST, decide whether this is a coherent, safe nutritional goal that can
            be scored from nutrition facts. Reject it if it is:
              - not a health/nutrition goal at all (gibberish, a joke, a product name)
              - not expressible as nutrient targets (e.g. "sleep better", "be happy")
              - nutritionally harmful or an eating-disorder pattern (e.g. "zero protein",
                "starve", "no food", "lose 10kg in a week", "muscle less")
              - so vague it has no defensible thresholds

            If you reject it, return EXACTLY:
            {{"valid": false, "reason": "<one short sentence, addressed to the user, explaining why this cannot be scored>"}}

            Do NOT invent a profile for a goal you would reject. Answering a nonsensical
            goal with plausible-looking numbers is worse than refusing.

            Otherwise return a JSON object in exactly this format:
            {{
              "valid": true,
              "label": "{goal_type.title()}",
              "prefer_low": ["nutrient_keys_here"],
              "prefer_high": ["nutrient_keys_here"],
              "thresholds": {{
                "energy_kcal": {{"good": 100, "bad": 300}},
                "nutrient_key": {{"good": 10, "bad": 2}}
              }},
              "weights": {{
                "energy_kcal": 1.0,
                "nutrient_key": 3.0
              }}
            }}
            
            Valid nutrient keys (ONLY use from this list): energy_kcal, protein_g, total_fat_g, saturated_fat_g, trans_fat_g, total_carbohydrates_g, total_sugars_g, fiber_g, sodium_mg, cholesterol_mg.
            Only include thresholds for nutrients that are highly relevant to this goal. "good" is the ideal value (or better), "bad" is the worst acceptable value before scoring 0.
            
            RULES:
            - For prefer_low nutrients: "good" should be LOWER than "bad" (good is the ceiling for excellent, bad is where score becomes 0)
            - For prefer_high nutrients: "good" should be HIGHER than "bad" (good is the floor for excellent, bad is where score becomes 0)
            - Include 3-6 nutrients maximum
            - Use realistic per-100g thresholds based on established nutritional science
            - Provide a 'weights' mapping for all included nutrients. Give primary macronutrients for the goal a higher weight (e.g., 2.0 or 3.0) and secondary nutrients a lower weight (e.g., 1.0).
            """
            
            from openai import AsyncOpenAI
            
            if settings.AI_PROVIDER.lower() in ("gemini", "google"):
                client = AsyncOpenAI(
                    api_key=settings.AI_API_KEY,
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
                )
                model = settings.AI_MODEL if settings.AI_MODEL.startswith("gemini") else "gemini-2.0-flash"
            elif settings.AI_PROVIDER.lower() == "groq":
                client = AsyncOpenAI(
                    api_key=settings.AI_API_KEY,
                    base_url="https://api.groq.com/openai/v1"
                )
                model = settings.AI_MODEL if "gpt" in settings.AI_MODEL or "compound" in settings.AI_MODEL or "qwen" in settings.AI_MODEL else "openai/gpt-oss-20b"
            else:
                client = AsyncOpenAI(api_key=settings.AI_API_KEY)
                model = settings.AI_MODEL
                
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You output JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            
            raw = response.choices[0].message.content or "{}"
            data = _parse_ai_response(raw)

            # ── Sanity gate ──
            # The model may decline to profile an incoherent, unscoreable, or
            # harmful goal. Honour that instead of storing invented thresholds.
            if data.get("valid") is False:
                reason = str(data.get("reason") or "").strip()
                if not reason:
                    reason = "This doesn't look like a nutritional goal we can score."
                print(f"🚫 AI rejected goal {goal_type!r}: {reason}")
                await _set_goal_status(
                    db, normalized_goal, GoalProfileStatus.UNSUPPORTED.value, reason
                )
                return

            thresholds = data.get("thresholds", {})
            prefer_low = data.get("prefer_low", [])
            prefer_high = data.get("prefer_high", [])

            if not thresholds:
                # A "valid" verdict with nothing to score is not usable either.
                print(f"⚠️ AI returned empty thresholds for goal: {goal_type}")
                await _set_goal_status(
                    db, normalized_goal, GoalProfileStatus.UNSUPPORTED.value,
                    "We couldn't work out how to measure this goal from nutrition labels.",
                )
                return

            # ── Validate and sanitize AI output ──
            
            # Filter to only valid nutrient keys
            valid_thresholds = {}
            for key, vals in thresholds.items():
                if key not in VALID_NUTRIENT_KEYS:
                    print(f"  ⚠️ Skipping invalid nutrient key: {key}")
                    continue
                if not isinstance(vals, dict) or "good" not in vals or "bad" not in vals:
                    print(f"  ⚠️ Skipping malformed threshold for {key}: {vals}")
                    continue
                    
                good_val = vals["good"]
                bad_val = vals["bad"]
                
                # Validate that good/bad relationship is correct for the direction
                if key in prefer_low:
                    # For prefer_low: good < bad (lower is better)
                    if good_val >= bad_val:
                        # Auto-fix: swap them
                        good_val, bad_val = bad_val, good_val
                elif key in prefer_high:
                    # For prefer_high: good > bad (higher is better)
                    if good_val <= bad_val:
                        # Auto-fix: swap them
                        good_val, bad_val = bad_val, good_val
                
                # Ensure values are positive numbers
                if not isinstance(good_val, (int, float)) or not isinstance(bad_val, (int, float)):
                    continue
                if good_val < 0 or bad_val < 0:
                    continue
                # Ensure good != bad (would cause division by zero)
                if good_val == bad_val:
                    continue
                    
                valid_thresholds[key] = {"good": good_val, "bad": bad_val}
            
            valid_prefer_low = [k for k in prefer_low if k in VALID_NUTRIENT_KEYS]
            valid_prefer_high = [k for k in prefer_high if k in VALID_NUTRIENT_KEYS]

            # Every scored nutrient must declare a direction. The engine skips
            # non-directional thresholds, so an AI profile that lists a nutrient
            # in `thresholds` but in neither preference list would silently drop
            # it. Infer the direction from the good/bad ordering instead.
            for key, vals in valid_thresholds.items():
                if key in valid_prefer_low or key in valid_prefer_high:
                    continue
                if vals["good"] < vals["bad"]:
                    valid_prefer_low.append(key)
                else:
                    valid_prefer_high.append(key)
                print(f"  ℹ️ Inferred direction for '{key}' from its thresholds.")

            # Drop direction entries that have no matching threshold — they can
            # never be scored and only confuse downstream consumers.
            valid_prefer_low = [k for k in valid_prefer_low if k in valid_thresholds]
            valid_prefer_high = [k for k in valid_prefer_high if k in valid_thresholds]

            raw_weights = data.get("weights", {})
            valid_weights = {}
            for k in valid_thresholds.keys():
                valid_weights[k] = float(raw_weights.get(k, 1.0))
            
            if not valid_thresholds:
                print(f"⚠️ No valid thresholds after validation for goal: {goal_type}")
                await _set_goal_status(
                    db, normalized_goal, GoalProfileStatus.UNSUPPORTED.value,
                    "We couldn't work out how to measure this goal from nutrition labels.",
                )
                return

            profile = CustomGoalProfile(
                goal_type=normalized_goal,
                label=data.get("label", goal_type.title()),
                prefer_low=valid_prefer_low,
                prefer_high=valid_prefer_high,
                thresholds=valid_thresholds,
                weights=valid_weights
            )
            db.add(profile)
            await db.commit()
            await _set_goal_status(db, normalized_goal, GoalProfileStatus.READY.value, None)
            print(f"✅ AI successfully generated math thresholds for custom goal: {goal_type}")
            print(f"   Thresholds: {json.dumps(valid_thresholds, indent=2)}")

    except Exception as e:
        print(f"⚠️ AI background task for custom goal generation failed for {goal_type}: {e}")
        traceback.print_exc()
        # Don't strand the goal in PENDING forever — the user needs to see that
        # it isn't contributing to their scores.
        try:
            async with AsyncSessionLocal() as db2:
                await _set_goal_status(
                    db2, normalized_goal, GoalProfileStatus.UNSUPPORTED.value, _UNAVAILABLE_MSG
                )
        except Exception:
            traceback.print_exc()
