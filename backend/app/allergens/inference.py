"""
AI allergen inference.

The synonym tables in the personalization engine cover the common allergens.
Anything outside them can currently only be matched by literal name, which
misses spelling variants ("dragonfruit" vs "dragon fruit"), translations,
E-numbers, and derived ingredients (copra for coconut, semolina for wheat).

This module asks an LLM to read the product's ingredient list and decide
whether a given allergen is present. Results are cached per (allergen,
product), so a given pair costs one model call ever.

Safety rules, enforced here and in the engine:
  - Inference is only consulted when string matching found nothing.
  - It can ADD a conflict, never clear one.
  - A "not found" verdict is never presented as proof the product is safe.
"""

import hashlib
import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import settings, _parse_ai_response
from app.allergens.models import AllergenInference
from app.personalization.engine import _normalize

# Bump when the prompt below changes materially — cached verdicts reached from
# an older prompt then stop matching and are recomputed.
PROMPT_VERSION = 1

# Cap how many ingredients we send, to bound prompt size on long labels.
_MAX_INGREDIENTS = 60
# Never ask about more than this many unresolved allergens for one product.
_MAX_ALLERGENS_PER_CALL = 8


def ingredients_fingerprint(
    product_ingredients: list[dict],
    product_allergens: list[dict],
) -> str:
    """
    Fingerprint what a verdict was based on: the product's ingredient names and
    its declared allergens.

    Names are normalized, deduplicated and sorted, so re-ordering a label or
    re-scraping it with different casing doesn't needlessly invalidate a cached
    verdict — only a genuine change of contents does.
    """
    ings = sorted({
        _normalize(str(i.get("name", "")))
        for i in product_ingredients
        if str(i.get("name", "")).strip()
    })
    declared = sorted({
        _normalize(str(a.get("allergen", "")))
        for a in product_allergens
        if str(a.get("allergen", "")).strip()
    })
    payload = "|".join(ings) + "||" + "|".join(declared)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_prompt(allergens: list[str], product_name: str, ingredients: list[str],
                  declared_allergens: list[str]) -> str:
    ing_text = ", ".join(ingredients) if ingredients else "(no ingredient list available)"
    dec_text = ", ".join(declared_allergens) if declared_allergens else "(none declared)"
    allergen_lines = "\n".join(f"  - {a}" for a in allergens)

    return f"""You are a food-safety analyst checking a product label for allergens.

Product: {product_name}
Declared allergens: {dec_text}
Ingredients: {ing_text}

For EACH of these allergens, decide whether this product contains it:
{allergen_lines}

Consider, for each allergen:
  - alternative names, spellings and spacings (e.g. "dragonfruit" = "dragon fruit")
  - other languages and scientific names
  - E-numbers and additive codes
  - derived or processed forms (e.g. copra/MCT from coconut, semolina from wheat,
    lecithin from soy, casein/whey from milk)
  - ingredients that virtually always contain it

Return JSON in exactly this shape:
{{
  "results": [
    {{
      "allergen": "<the allergen name exactly as given above>",
      "found": true or false,
      "certainty": "high" | "medium" | "low",
      "matched_ingredient": "<the ingredient that contains it, or null>",
      "reason": "<one short sentence>"
    }}
  ]
}}

RULES:
- "high" certainty ONLY when an ingredient definitely contains the allergen.
- Use "medium" or "low" for derived, ambiguous, or may-contain cases.
- If the ingredient list is empty or uninformative, return found=false with
  certainty "low".
- Do NOT guess based on the product name alone.
- Health is at stake: if genuinely unsure whether something contains it, return
  found=true with "low" certainty rather than false."""


def resolve_model() -> str:
    """
    The model this provider will actually use.

    Resolved separately from the call because it is part of the cache key — we
    need it before deciding whether a cached verdict applies.
    """
    provider = settings.AI_PROVIDER.lower()
    if provider in ("gemini", "google"):
        return settings.AI_MODEL if settings.AI_MODEL.startswith("gemini") else "gemini-2.0-flash"
    if provider == "groq":
        from app.ai.gateway import KNOWN_GROQ_MODELS, GROQ_DEFAULT_MODEL
        return settings.AI_MODEL if settings.AI_MODEL in KNOWN_GROQ_MODELS else GROQ_DEFAULT_MODEL
    return settings.AI_MODEL


async def _call_model(prompt: str, model: str) -> dict:
    """Run the prompt against the configured provider."""
    from openai import AsyncOpenAI

    provider = settings.AI_PROVIDER.lower()
    if provider in ("gemini", "google"):
        client = AsyncOpenAI(
            api_key=settings.AI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
    elif provider == "groq":
        client = AsyncOpenAI(api_key=settings.AI_API_KEY, base_url="https://api.groq.com/openai/v1")
    else:
        client = AsyncOpenAI(api_key=settings.AI_API_KEY)

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a food-safety analyst. You output JSON only."},
            {"role": "user", "content": prompt},
        ],
        # Low temperature: this is a factual label-reading task, not a creative one.
        temperature=0.1,
        max_tokens=1200,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    return _parse_ai_response(raw)


async def resolve_allergens(
    db: AsyncSession,
    product_id: int,
    product_name: str,
    unresolved: list[str],
    product_ingredients: list[dict],
    product_allergens: list[dict],
    allow_ai: bool = True,
) -> dict[str, dict]:
    """
    Decide whether this product contains each of `unresolved`.

    Returns {normalized_allergen: {found, certainty, matched_ingredient, reason}}
    for every allergen we reached a verdict on. Allergens absent from the result
    were not checked, and the engine keeps its "limited coverage" disclosure
    for those.

    `allow_ai=False` returns cached verdicts only — used on paths that score
    many products at once, where firing a model call per candidate would be
    prohibitively slow.
    """
    if not unresolved:
        return {}

    wanted = list(dict.fromkeys(_normalize(a) for a in unresolved if a and a.strip()))
    if not wanted:
        return {}

    # ── Cache lookup ──
    # Scoped to everything that determines the verdict: the label it was read
    # from, the model that read it, and the prompt revision we asked with. Any
    # of those differing makes a cached row a miss rather than truth.
    fingerprint = ingredients_fingerprint(product_ingredients, product_allergens)
    model = resolve_model()
    cached_rows = (await db.execute(
        select(AllergenInference).where(
            AllergenInference.product_id == product_id,
            AllergenInference.allergen.in_(wanted),
            AllergenInference.ingredients_hash == fingerprint,
            AllergenInference.model == model,
            AllergenInference.prompt_version == PROMPT_VERSION,
        )
    )).scalars().all()

    results: dict[str, dict] = {
        row.allergen: {
            "found": row.found,
            "certainty": row.certainty,
            "matched_ingredient": row.matched_ingredient,
            "reason": row.reason,
        }
        for row in cached_rows
    }

    missing = [a for a in wanted if a not in results]
    if not missing or not allow_ai or not settings.AI_API_KEY:
        return results

    missing = missing[:_MAX_ALLERGENS_PER_CALL]

    ingredients = [
        str(i.get("name", "")).strip()
        for i in product_ingredients[:_MAX_INGREDIENTS]
        if str(i.get("name", "")).strip()
    ]
    declared = [
        str(a.get("allergen", "")).strip()
        for a in product_allergens
        if str(a.get("allergen", "")).strip()
    ]

    try:
        data = await _call_model(
            _build_prompt(missing, product_name, ingredients, declared), model
        )
    except Exception as e:
        # Inference is an enhancement — never let it break scoring.
        print(f"⚠️ Allergen inference failed for product {product_id}: {e}")
        return results

    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        print(f"⚠️ Allergen inference returned unexpected shape for product {product_id}")
        return results

    by_name = {_normalize(str(a)): a for a in missing}
    to_store: list[dict] = []

    for item in raw_results:
        if not isinstance(item, dict):
            continue
        name = _normalize(str(item.get("allergen", "")))
        if name not in by_name:
            continue

        found = bool(item.get("found"))
        certainty = _normalize(str(item.get("certainty", "medium")))
        if certainty not in ("high", "medium", "low"):
            certainty = "medium"

        matched = item.get("matched_ingredient")
        matched = str(matched).strip()[:255] if matched else None
        reason = item.get("reason")
        reason = str(reason).strip() if reason else None

        verdict = {
            "found": found,
            "certainty": certainty,
            "matched_ingredient": matched,
            "reason": reason,
        }
        results[name] = verdict
        to_store.append({
            "allergen": name,
            "product_id": product_id,
            "ingredients_hash": fingerprint,
            "found": found,
            "certainty": certainty,
            "matched_ingredient": matched,
            "reason": reason,
            "model": model,
            "prompt_version": PROMPT_VERSION,
        })

    if to_store:
        # Upsert on (allergen, product_id, model, prompt_version): a stale
        # verdict for the same model+prompt is replaced when the label changes,
        # while verdicts from other models or prompt revisions are left intact.
        stmt = pg_insert(AllergenInference).values(to_store)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_allergen_inference",
            set_={
                "ingredients_hash": stmt.excluded.ingredients_hash,
                "found": stmt.excluded.found,
                "certainty": stmt.excluded.certainty,
                "matched_ingredient": stmt.excluded.matched_ingredient,
                "reason": stmt.excluded.reason,
                "model": stmt.excluded.model,
            },
        )
        try:
            await db.execute(stmt)
            await db.commit()
        except Exception as e:
            # Caching is best-effort — the verdict is still valid for this
            # response even if we could not persist it.
            await db.rollback()
            print(f"⚠️ Could not cache allergen inference for product {product_id}: {e}")

    return results
