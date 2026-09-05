"""
ARIVIO AI Gateway — Provider-Independent AI Service

Supports multiple LLM providers through a unified interface.
Providers: OpenAI (GPT), Google (Gemini), Groq (Llama), or local fallback (rule-based).

The gateway handles:
1. Provider selection based on configuration
2. Prompt construction from structured suitability data
3. Response parsing and validation
4. Graceful fallback if AI providers are unavailable
"""

import json
from typing import Optional
from dataclasses import dataclass
from app.core.config import get_settings

settings = get_settings()

# Models Groq actually serves. Anything else falls back to the default rather
# than being sent upstream and failing at request time.
KNOWN_GROQ_MODELS = {
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "groq/compound",
    "groq/compound-mini",
}
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"

# Fallback model when AI_PROVIDER is Gemini but AI_MODEL names something else
# (a Groq or OpenAI model, say). Defined here and imported by the other Gemini
# call sites — it was previously written out at five of them, all still saying
# "gemini-2.0-flash", which Google has since retired: every one of them now
# answers 404 "no longer available". One constant so the next retirement is a
# one-line fix instead of five.
#
# Google retires these on a schedule, so treat it as perishable. If Gemini
# calls start failing with 404, list what the key can actually reach:
#     curl -H "Authorization: Bearer $OCR_API_KEY" \
#       https://generativelanguage.googleapis.com/v1beta/openai/models
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"


@dataclass
class AIReport:
    """Structured AI-generated product report."""
    summary: str                     # 2-3 sentence overview
    detailed_analysis: str           # Full markdown analysis
    key_insights: list[str]          # Bullet-point insights
    recommendations: list[str]      # Actionable recommendations
    confidence_note: str             # AI confidence disclaimer
    provider: str                    # Which AI provider generated this
    model: str                       # Which model was used


def _build_prompt(
    product_name: str,
    product_brand: Optional[str],
    product_category: Optional[str],
    suitability_score: int,
    verdict: str,
    allergen_safe: bool,
    flags: list[dict],
    goal_alignments: list[dict],
    nutrition: Optional[dict],
    ingredients: list[dict],
    user_goals: list[dict],
    user_allergies: list[dict],
    user_feedback_examples: list[dict] = None,
) -> str:
    """Build a structured prompt for the LLM."""

    # Build product context
    product_info = f"**Product:** {product_name}"
    if product_brand:
        product_info += f" by {product_brand}"
    if product_category:
        product_info += f" (Category: {product_category})"

    # Build nutrition summary
    nutrition_text = "Not available"
    if nutrition:
        nutrition_parts = []
        nutrient_labels = {
            "energy_kcal": "Energy",
            "protein_g": "Protein",
            "total_fat_g": "Total Fat",
            "saturated_fat_g": "Saturated Fat",
            "total_carbohydrates_g": "Carbs",
            "total_sugars_g": "Sugars",
            "fiber_g": "Fiber",
            "sodium_mg": "Sodium",
        }
        for key, label in nutrient_labels.items():
            val = nutrition.get(key)
            if val is not None:
                unit = "mg" if "mg" in key else ("kcal" if "kcal" in key else "g")
                nutrition_parts.append(f"{label}: {val}{unit}")
        if nutrition_parts:
            nutrition_text = ", ".join(nutrition_parts)

    # Build ingredients summary
    ingredient_names = [ing.get("name", "") for ing in ingredients[:20]]
    ingredients_text = ", ".join(ingredient_names) if ingredient_names else "Not available"

    # Build flags summary
    flags_text = ""
    for flag in flags:
        icon = "🔴" if flag["flag_type"] == "danger" else "🟡" if flag["flag_type"] == "warning" else "🟢" if flag["flag_type"] == "positive" else "ℹ️"
        flags_text += f"\n  {icon} {flag['title']}: {flag['description']} (Impact: {flag['impact']:+d})"

    # Build goal alignment summary
    goals_text = ""
    for ga in goal_alignments:
        if ga.get("evaluated", True):
            goals_text += f"\n  - {ga['goal']}: {ga['alignment']} (Score: {ga['score']}/100) — {ga['reason']}"
        else:
            # Never let the model narrate a score we did not actually compute.
            goals_text += (
                f"\n  - {ga['goal']}: NOT EVALUATED — no scoring profile exists for this goal, "
                "so it was excluded from the overall score. Say plainly that this goal could not "
                "be assessed; do NOT invent an alignment or imply the product suits it."
            )

    # Build user context
    user_goals_text = ", ".join([g.get("goal_type", "general_health") for g in user_goals]) if user_goals else "General Health"
    user_allergies_text = ", ".join([a.get("allergen", "") for a in user_allergies]) if user_allergies else "None declared"
    
    # Build feedback context — include ALL feedback with rating context
    feedback_text = ""
    if user_feedback_examples:
        ratings = [
            fbk.get("rating") for fbk in user_feedback_examples
            if isinstance(fbk.get("rating"), (int, float))
        ]
        avg_rating = sum(ratings) / len(ratings) if ratings else None

        feedback_text = (
            "\n\n## Past User Feedback (CRITICAL GUIDANCE)\n"
            "The user has provided the following feedback on previous reports. "
            "Adapt your tone, structure, and focus to align with these preferences:\n"
        )
        for fbk in user_feedback_examples:
            rating = fbk.get("rating", 3)
            comment = (fbk.get("comment") or "").strip()
            feedback_type = fbk.get("feedback_type", "general")
            sentiment = "👍 POSITIVE" if rating >= 4 else "👎 NEGATIVE" if rating <= 2 else "😐 NEUTRAL"
            # A rating with no comment is still signal — say so explicitly rather
            # than emitting a dangling bullet the model will ignore.
            body = comment if comment else "(rated only, no written comment)"
            feedback_text += f"- [{sentiment}, {rating}/5 stars, type: {feedback_type}] {body}\n"

        if avg_rating is not None:
            feedback_text += f"\nAverage rating across these reports: {avg_rating:.1f}/5.\n"
            if avg_rating <= 2.5:
                feedback_text += (
                    "This user has been consistently dissatisfied. Change your approach "
                    "materially — be more specific and concrete, cut filler, and lead with "
                    "what actually affects their decision.\n"
                )
        feedback_text += "\nPay special attention to negative feedback — those highlight what the user wants changed.\n"

    prompt = f"""You are ARIVIO, a personalized product intelligence assistant. Analyze this product evaluation and generate a clear, helpful report for the user.

## Product Data
{product_info}

**Nutrition (per 100g):** {nutrition_text}
**Ingredients:** {ingredients_text}
**Total ingredients count:** {len(ingredients)}

## Personal Suitability Analysis
**Overall Score:** {suitability_score}/100
**Verdict:** {verdict}
**Allergen Safe:** {"Yes ✓" if allergen_safe else "NO ✗ — ALLERGEN CONFLICT DETECTED"}

**Analysis Flags:**{flags_text or " None"}

**Goal Alignment:**{goals_text or " No goals set"}

## User Profile
**Health Goals:** {user_goals_text}
**Declared Allergies:** {user_allergies_text}{feedback_text}

---

Generate a JSON response with the following structure:
{{
  "summary": "A 2-3 sentence overview explaining what this product means for the user's health goals. Be direct and personal. Use 'you/your' language.",
  "detailed_analysis": "A thorough markdown-formatted analysis covering: 1) Nutritional highlights, 2) Ingredient quality assessment, 3) Goal alignment explanation, 4) Any concerns. Use headers (##), bullet points, and bold text. Keep it conversational but evidence-based. 4-6 paragraphs.",
  "key_insights": ["3-5 most important takeaways as bullet points"],
  "recommendations": ["2-4 actionable recommendations specific to this user's goals"]
}}

IMPORTANT RULES:
- Be honest and balanced — highlight both positives and negatives
- If allergen conflict exists, make it the FIRST and MOST PROMINENT point
- Relate everything back to the user's declared health goals
- Use simple, non-medical language that anyone can understand
- Do NOT make medical claims or diagnose conditions
- Flags with category "health" came from lab results the user uploaded
  themselves. You may say what the product does relative to those readings
  ("this is high in sodium, and your recent results flagged kidney
  function"), but you must NEVER name a disease, state or imply a diagnosis,
  or tell them to start or stop a medication. Refer to "your recent results",
  not to a condition. Suggest speaking to their doctor or a dietitian rather
  than presenting your reading as clinical guidance.
- Keep the tone helpful and empowering, not scary
- Be specific: reference actual numbers from the nutrition data
- Nutrition figures are per 100g, NOT per serving — say "per 100g" when you
  quote a number, and never describe one as a serving amount"""

    return prompt


def _coerce_str_list(value) -> list[str]:
    """
    Coerce an LLM-supplied field into a clean list of strings.

    Models routinely return a bare string, a list of dicts, or nulls where a
    list of strings was asked for. Without this the malformed value flows
    straight into the response schema and 500s the endpoint.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, (list, tuple)):
        return [str(value).strip()]

    out: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, dict):
            # e.g. [{"insight": "..."}] — take the first usable string value
            item = next((v for v in item.values() if isinstance(v, str) and v.strip()), None)
            if item is None:
                continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _parse_ai_response(raw: str) -> dict:
    """Parse AI response, handling markdown code blocks and edge cases."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON from AI: {e}\nRaw output: {raw}")
        raise ValueError("AI returned invalid JSON structure.")


SYSTEM_PROMPT = (
    "You are ARIVIO, a personalized product intelligence AI. Always respond "
    "with valid JSON only, no markdown code blocks."
)

CONFIDENCE_NOTE = (
    "This analysis is AI-generated based on available product data and your "
    "health profile. Always consult a healthcare professional for medical "
    "dietary advice."
)


def _report_from_data(data: dict, provider: str, model: str) -> AIReport:
    """
    Build a report from one provider's JSON.

    Every field is coerced rather than trusted: models routinely return a bare
    string, a list of dicts, or nulls where a list of strings was asked for,
    and a malformed value flowing into the response schema 500s the endpoint.
    """
    return AIReport(
        summary=str(data.get("summary") or "Analysis complete."),
        detailed_analysis=str(data.get("detailed_analysis") or ""),
        key_insights=_coerce_str_list(data.get("key_insights")),
        recommendations=_coerce_str_list(data.get("recommendations")),
        confidence_note=CONFIDENCE_NOTE,
        provider=provider,
        model=model,
    )


def _generate_fallback(
    product_name: str,
    suitability_score: int,
    verdict: str,
    allergen_safe: bool,
    flags: list[dict],
    goal_alignments: list[dict],
    user_feedback_examples: list[dict] = None,
) -> AIReport:
    """Generate a rule-based report when no AI provider is available."""

    # Build summary
    if not allergen_safe:
        summary = f"⚠️ This product contains allergens from your profile and is not recommended for you. Your personal suitability score is {suitability_score}/100."
    elif suitability_score >= 70:
        summary = f"This product is a good match for your health goals with a suitability score of {suitability_score}/100. {verdict}."
    elif suitability_score >= 40:
        summary = f"This product has moderate suitability ({suitability_score}/100) for your health profile. Review the details below to understand the trade-offs."
    else:
        summary = f"This product scores {suitability_score}/100 for your profile, indicating low suitability. Consider alternatives that better match your health goals."

    # Build detailed analysis from flags
    analysis_parts = ["## Product Analysis\n"]

    if not allergen_safe:
        danger_flags = [f for f in flags if f["flag_type"] == "danger"]
        analysis_parts.append("### ⚠️ Allergen Alert\n")
        for f in danger_flags:
            analysis_parts.append(f"- **{f['title']}:** {f['description']}\n")
        analysis_parts.append("\n")

    # Positive aspects
    positive_flags = [f for f in flags if f["flag_type"] == "positive"]
    if positive_flags:
        analysis_parts.append("### ✅ What's Good\n")
        for f in positive_flags:
            analysis_parts.append(f"- **{f['title']}:** {f['description']}\n")
        analysis_parts.append("\n")

    # Concerns
    warning_flags = [f for f in flags if f["flag_type"] == "warning"]
    if warning_flags:
        analysis_parts.append("### ⚠️ Points of Concern\n")
        for f in warning_flags:
            analysis_parts.append(f"- **{f['title']}:** {f['description']}\n")
        analysis_parts.append("\n")

    # Goal alignment
    if goal_alignments:
        analysis_parts.append("### 🎯 Goal Alignment\n")
        for ga in goal_alignments:
            # A goal with no profile has score None — render it as not assessed
            # rather than "Not_evaluated alignment (Score: None/100)".
            if not ga.get("evaluated", True) or ga.get("score") is None:
                analysis_parts.append(f"- ⚠️ **{ga['goal']}:** not assessed\n")
            else:
                emoji = "✅" if ga["alignment"] in ("excellent", "good") else "⚠️" if ga["alignment"] == "neutral" else "❌"
                analysis_parts.append(f"- {emoji} **{ga['goal']}:** {ga['alignment'].capitalize()} alignment (Score: {ga['score']}/100)\n")
            if ga.get("reason"):
                analysis_parts.append(f"  _{ga['reason']}_\n")

    detailed_analysis = "".join(analysis_parts)

    # Key insights
    key_insights = []
    if not allergen_safe:
        key_insights.append("This product contains allergens that match your declared allergies — avoid or proceed with extreme caution.")
    for f in positive_flags[:2]:
        key_insights.append(f"{f['title']}: {f['description']}")
    for f in warning_flags[:2]:
        key_insights.append(f"{f['title']}: {f['description']}")
    if not key_insights:
        key_insights.append(f"Overall suitability score: {suitability_score}/100")

    # Recommendations
    recommendations = []
    if not allergen_safe:
        recommendations.append("Look for allergen-free alternatives in the same product category.")
    if suitability_score < 40:
        recommendations.append("Consider products with simpler ingredient lists and better nutritional profiles for your goals.")
    for ga in goal_alignments:
        if ga["alignment"] in ("poor", "bad"):
            recommendations.append(f"For your {ga['goal']} goal, look for products with better alignment scores.")
            break
    if not recommendations:
        recommendations.append("This product fits your profile well. Enjoy it as part of a balanced diet.")

    # ── Adapt to past feedback ──
    # The rule-based path can't rewrite its prose the way an LLM can, but it can
    # still respond to the two feedback types that map onto concrete changes.
    if user_feedback_examples:
        types = [(f.get("feedback_type") or "general").lower() for f in user_feedback_examples]
        ratings = [
            f.get("rating") for f in user_feedback_examples
            if isinstance(f.get("rating"), (int, float))
        ]
        avg_rating = sum(ratings) / len(ratings) if ratings else None

        if types.count("incomplete") >= 2:
            # Surface every flag rather than the trimmed selection above.
            key_insights = [f"{f['title']}: {f['description']}" for f in flags] or key_insights
            for ga in goal_alignments:
                # Same guard as the analysis section above: a goal with no
                # profile has score None, and "scored None/100 (not_evaluated
                # alignment)" is worse than saying it wasn't assessed.
                if not ga.get("evaluated", True) or ga.get("score") is None:
                    recommendations.append(
                        f"{ga['goal']}: not assessed — no scoring profile exists for this goal."
                    )
                else:
                    recommendations.append(
                        f"{ga['goal']}: scored {ga['score']}/100 ({ga['alignment']} alignment)."
                    )

        if types.count("inaccurate") >= 2:
            recommendations.append(
                "You've flagged previous reports as inaccurate — double-check the on-pack "
                "label, as our product data may be incomplete or out of date."
            )

        if avg_rating is not None and avg_rating <= 2.5:
            recommendations.append(
                "Your ratings suggest these reports aren't landing. Configuring an AI "
                "provider enables far more tailored analysis than this rule-based summary."
            )

    return AIReport(
        summary=summary,
        detailed_analysis=detailed_analysis,
        key_insights=key_insights,
        recommendations=recommendations,
        confidence_note="This analysis was generated using rule-based logic. For AI-powered insights, configure an API key in your settings.",
        provider="fallback",
        model="rule-based",
    )


async def generate_report(
    product_name: str,
    product_brand: Optional[str],
    product_category: Optional[str],
    suitability_score: int,
    verdict: str,
    allergen_safe: bool,
    flags: list[dict],
    goal_alignments: list[dict],
    nutrition: Optional[dict],
    ingredients: list[dict],
    user_goals: list[dict],
    user_allergies: list[dict],
    user_feedback_examples: list[dict] = None,
) -> AIReport:
    """
    Generate an AI-powered product report.

    Walks the provider chain (gemini → groq → cerebras → openrouter) and takes
    the first success. The rule-based fallback runs only when every configured
    provider has failed.

    That ordering is the point of the chain. Previously a single provider was
    tried once, so one transient error — most often a reasoning model spending
    its token budget on thought and being cut off mid-JSON — dropped the user
    to rule-based prose even though three other providers were sitting idle.
    """
    from app.ai.providers import AllProvidersFailed, complete_json, resolve_chain

    def fallback() -> AIReport:
        return _generate_fallback(
            product_name, suitability_score, verdict,
            allergen_safe, flags, goal_alignments,
            user_feedback_examples,
        )

    # No provider configured at all is not a failure to report on; it is the
    # documented no-key mode.
    if not resolve_chain():
        return fallback()

    prompt = _build_prompt(
        product_name=product_name,
        product_brand=product_brand,
        product_category=product_category,
        suitability_score=suitability_score,
        verdict=verdict,
        allergen_safe=allergen_safe,
        flags=flags,
        goal_alignments=goal_alignments,
        nutrition=nutrition,
        ingredients=ingredients,
        user_goals=user_goals,
        user_allergies=user_allergies,
        user_feedback_examples=user_feedback_examples,
    )

    try:
        result = await complete_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            # A full report is 1,000-1,500 tokens of visible output, and a
            # reasoning model spends more than that again before it starts
            # writing. The old 2,000 ceiling cut it off mid-JSON often enough
            # to look like the AI was simply broken.
            max_tokens=4000,
            temperature=0.4,
        )
        return _report_from_data(result.data, result.provider, result.model)
    except AllProvidersFailed as e:
        print(f"⚠️ {e}")
        print("Falling back to rule-based report generation.")
        return fallback()
