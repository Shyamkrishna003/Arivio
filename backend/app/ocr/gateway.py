"""
ARIVIO OCR Gateway — provider-independent label reading.

Mirrors app.ai.gateway: one entry point, several providers, and a local
fallback that always works. The difference is what it returns — the AI gateway
produces prose for a person to read, this one produces structured data that
becomes a product, so it validates hard and degrades to "not detected" rather
than to a plausible guess.

Why a vision model rather than classical OCR:

PRD §14 specifies a pipeline of Preprocessing → OCR → Text Extraction →
Ingredient/Nutrition Parsing → Normalization. Tesseract only performs the OCR
step; the parsing and normalization of a wrapped ingredient list and a
two-column nutrition panel are each substantial work, and Tesseract's raw
output on curved, glossy packaging is poor enough that the parser would spend
its life on repair. A vision model collapses all four steps into one call and
reads packaging far better. Tesseract stays as the no-API-key fallback, and is
honest about being weaker.

Safety rules, mirroring the allergen inference module:
  - Nothing extracted here reaches a product without user confirmation.
  - A field that could not be read stays empty. Never inferred, never
    defaulted, never filled in from the product name.
  - Extraction confidence is reported, not acted on.
"""

import base64
import json
from typing import Optional

from app.core.config import get_settings
from app.ocr.schemas import ExtractedNutrition, LabelExtraction

settings = get_settings()

# Bump when the prompt below changes materially, so cached extractions from an
# older prompt stop being reused.
PROMPT_VERSION = 1

# Vision-capable defaults per provider, used when OCR_MODEL is unset. The
# configured chat model is not reused: AI_MODEL is routinely a text-only model
# (the default, openai/gpt-oss-20b, cannot see an image at all), and sending an
# image to it fails at request time rather than falling back.
DEFAULT_VISION_MODELS = {
    # Measured against this repo's sample label (test/seed_label_scan.py) and
    # a deliberately degraded copy of it — blurred, rotated, low-contrast,
    # heavily compressed, with a glare blob over one corner:
    #
    #                          clean image        degraded image
    #   gemini-3.5-flash-lite  2.0s  4/4, 7/7     2.8s   4/4, 5/7
    #   gemini-3.6-flash       23s   4/4, 7/7     34s    4/4, 5/7
    #   gemini-3.1-flash-lite  3.1s  4/4, 7/7     4.5s   4/4, 2/7
    #
    # (ingredients matched / nutrients matched exactly)
    #
    # flash-lite 3.5 reads labels as accurately as the full flash model at a
    # tenth of the latency, and that latency is on a user's critical path —
    # so it is the default. 3.1-flash-lite is not: it degraded badly on the
    # harder image, misreading five of seven figures by around +0.9 each.
    #
    # Override with OCR_MODEL if your labels are consistently harder than the
    # test images; gemini-3.6-flash is the sturdier, slower choice.
    "gemini": "gemini-3.5-flash-lite",
    "openai": "gpt-4o-mini",
    # Groq's vision line-up varies by account and changes often — this
    # project's own Groq account currently serves no vision model at all. Set
    # OCR_MODEL to one your account actually lists (GET /openai/v1/models, or
    # test/fetch_models.py) rather than relying on this default.
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
}

# Providers "auto" may select on its own. Gemini and OpenAI are here because
# their default models above are reliably vision-capable on any account with a
# key. Groq is deliberately excluded: picking it automatically would send an
# image to a model the account may not serve, and the request only fails once
# it has already cost the user the latency. A Groq deployment opts in
# explicitly with OCR_PROVIDER=groq.
AUTO_VISION_PROVIDERS = {"gemini", "openai"}

PROVIDER_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "openai": None,   # the SDK's own default
}

# Ingredient lists are long but not unbounded; this stops a hallucinated loop
# from writing hundreds of rows into a product.
MAX_INGREDIENTS = 120
MAX_INGREDIENT_LENGTH = 200


_PROMPT = """You are reading a photograph of consumer product packaging.

Transcribe ONLY what is legible in the image. This data will be used to warn
people about allergens, so a wrong value is far worse than a missing one.

Rules:
- Never guess, infer, complete or correct anything. If a word is blurred,
  cut off, or hidden, omit it.
- Do NOT infer ingredients from the product name or from what such a product
  usually contains. Transcribe only the printed ingredient list.
- Keep ingredients in the printed order — it is ordered by descending
  quantity and the analysis depends on that order.
- Keep sub-ingredients in parentheses as part of their parent entry, exactly
  as printed, e.g. "chocolate (cocoa mass, sugar, emulsifier: soy lecithin)".
- Nutrition values must be PER 100g or PER 100ml. If the panel is stated per
  serving and gives no per-100 column, leave nutrition empty and add a
  warning saying so. Do not convert.
- Use the printed units. Sodium in mg, everything else in g, energy in kcal.
  If energy is only in kJ, divide by 4.184 to get kcal.
- If the image is too blurry, dark or angled to read confidently, say so in
  warnings and return whatever you could read.

Return ONLY a JSON object of this exact shape:
{
  "label_type": "front" | "ingredients" | "nutrition" | "mixed" | "unknown",
  "name": "product name as printed, or null",
  "brand": "brand as printed, or null",
  "category": "a short generic category such as 'Biscuits', or null",
  "serving_size": "as printed, e.g. '30 g', or null",
  "barcode": "digits only if a barcode number is printed legibly, else null",
  "ingredients": ["in printed order", "..."],
  "nutrition": {
    "energy_kcal": null, "protein_g": null, "total_fat_g": null,
    "saturated_fat_g": null, "trans_fat_g": null, "cholesterol_mg": null,
    "total_carbohydrates_g": null, "total_sugars_g": null,
    "added_sugars_g": null, "dietary_fiber_g": null, "sodium_mg": null
  },
  "confidence": 0.0 to 1.0,
  "warnings": ["anything the user should double-check"]
}

Use null for anything not visible. Use [] for an absent ingredient list."""


def resolve_provider() -> str:
    """
    Decide which provider reads the label.

    "auto" prefers a vision model when a key is available and falls back to
    tesseract, so a deployment with no AI key still has a working scan path
    rather than a broken button.
    """
    provider = (settings.OCR_PROVIDER or "auto").lower()

    if provider == "auto":
        if resolve_api_key():
            configured = (settings.AI_PROVIDER or "").lower()
            configured = "gemini" if configured == "google" else configured
            if configured in AUTO_VISION_PROVIDERS:
                return configured
            # A key exists but not for a provider we can assume reads images.
            # Tesseract at least works, and says in its own warnings that a
            # vision model would do far better.
        return "tesseract"

    if provider == "google":
        return "gemini"
    return provider


def resolve_api_key() -> str:
    """OCR-specific key if set, otherwise the shared AI key."""
    return settings.OCR_API_KEY or settings.AI_API_KEY or ""


def resolve_model(provider: str) -> str:
    """
    The model to read with.

    OCR_MODEL wins if set, but only for the provider it belongs to — a model
    name is not portable, and silently sending a Gemini model id to Groq fails
    at request time.
    """
    if provider == "tesseract":
        # Not a model, but it is part of the extraction cache key, and naming
        # a vision model there would be actively misleading — a read done by
        # tesseract must not share a cache entry with one done by a model.
        return "tesseract"
    if settings.OCR_MODEL:
        return settings.OCR_MODEL
    return DEFAULT_VISION_MODELS.get(provider, "gpt-4o-mini")


def _coerce_ingredients(value) -> list[str]:
    """
    Coerce the model's ingredient field into clean ordered strings.

    Models return a bare comma-separated string, a list of dicts, or nulls
    where a list of strings was asked for. Order is preserved and duplicates
    are dropped, because ingredient order carries meaning here.
    """
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, (list, tuple)):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if item is None:
            continue
        if isinstance(item, dict):
            item = next(
                (v for v in item.values() if isinstance(v, str) and v.strip()), None
            )
            if item is None:
                continue
        text = str(item).strip().strip(".").strip()
        if not text or len(text) > MAX_INGREDIENT_LENGTH:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= MAX_INGREDIENTS:
            break
    return out


def _coerce_str(value) -> Optional[str]:
    """A trimmed string, or None for anything empty or model-speak for absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown", "not visible"}:
        return None
    return text


def _coerce_warnings(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


def valid_gtin(digits: str) -> bool:
    """
    Whether a digit string is a well-formed GTIN-8/12/13/14 barcode number.

    Every retail barcode carries a check digit computed from the others, which
    makes this a genuine test rather than a length check: a single misread or
    transposed digit fails it about 90% of the time. That is what lets an
    OCR-read barcode be used for a database lookup at all — without the
    checksum it would just be five-to-fourteen digits of hope.
    """
    if not digits.isdigit() or len(digits) not in (8, 12, 13, 14):
        return False
    # Weights alternate 3 and 1 from the right, excluding the check digit.
    body, check = digits[:-1], int(digits[-1])
    total = sum(
        int(d) * (3 if i % 2 == 0 else 1)
        for i, d in enumerate(reversed(body))
    )
    return (10 - total % 10) % 10 == check


def _coerce_confidence(value) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    # Models sometimes answer on a 0-100 scale despite being asked for 0-1.
    if confidence > 1.0:
        confidence = confidence / 100.0
    return max(0.0, min(1.0, confidence))


def parse_extraction(data: dict, provider: str, model: str) -> LabelExtraction:
    """
    Build a validated LabelExtraction from a model's raw JSON.

    Every field is coerced rather than trusted. ExtractedNutrition's own
    validators then drop physically impossible figures, so a misplaced decimal
    becomes "not detected" instead of a number the user might not question.
    """
    raw_nutrition = data.get("nutrition")
    if not isinstance(raw_nutrition, dict):
        raw_nutrition = {}
    # Ignore keys we did not ask for rather than failing the whole read.
    known = set(ExtractedNutrition.model_fields)
    nutrition = ExtractedNutrition(**{
        k: v for k, v in raw_nutrition.items() if k in known
    })

    label_type = (_coerce_str(data.get("label_type")) or "unknown").lower()
    if label_type not in {"front", "ingredients", "nutrition", "mixed", "unknown"}:
        label_type = "unknown"

    barcode = _coerce_str(data.get("barcode"))
    if barcode:
        digits = "".join(c for c in barcode if c.isdigit())
        # Discarded unless the check digit agrees. A barcode read off a
        # photograph is used to look a product up directly, so a misread one
        # would confidently return the wrong product — worse than none.
        barcode = digits if valid_gtin(digits) else None

    return LabelExtraction(
        label_type=label_type,
        name=_coerce_str(data.get("name")),
        brand=_coerce_str(data.get("brand")),
        category=_coerce_str(data.get("category")),
        serving_size=_coerce_str(data.get("serving_size")),
        barcode=barcode,
        ingredients=_coerce_ingredients(data.get("ingredients")),
        nutrition=nutrition,
        confidence=_coerce_confidence(data.get("confidence")),
        warnings=_coerce_warnings(data.get("warnings")),
        provider=provider,
        model=model,
    )


def build_vision_messages(image: bytes, mime: str) -> list:
    """The label-reading request, as chat messages."""
    data_url = f"data:{mime};base64,{base64.b64encode(image).decode('ascii')}"
    return [
        {
            "role": "system",
            "content": (
                "You transcribe product labels. You output valid JSON only, "
                "with no markdown code fences. You never guess at text you "
                "cannot clearly see."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


async def _extract_with_vision(image: bytes, mime: str, provider: str) -> LabelExtraction:
    """
    Read the label through the vision provider chain.

    Only providers with a vision model configured take part, so a Groq-only
    deployment is skipped here rather than being sent an image it will reject.
    A transient 503 from one provider now moves to the next instead of
    dropping the user to a blank confirmation form.
    """
    from app.ai.providers import complete_json

    result = await complete_json(
        build_vision_messages(image, mime),
        # Transcription, not composition.
        temperature=0.0,
        # A dense nutrition panel plus a long ingredient list runs well past
        # the old 2,000 ceiling, and a truncated read is rejected outright.
        max_tokens=4000,
        vision=True,
        timeout=settings.OCR_TIMEOUT,
    )
    return parse_extraction(result.data, result.provider, result.model)


def _extract_with_tesseract(image: bytes) -> LabelExtraction:
    """
    Local fallback: raw OCR, no parsing.

    Deliberately modest. Tesseract returns unstructured text, and guessing
    which line is a product name or reconstructing a nutrition panel from it
    is exactly the inference this module refuses to do elsewhere. So it
    returns the text as ingredient candidates for the user to correct, and
    says plainly that it did no parsing.

    Requires the tesseract binary. The Dockerfile installs it only when
    OCR_PROVIDER may resolve here.
    """
    import io

    import pytesseract
    from PIL import Image

    text = pytesseract.image_to_string(Image.open(io.BytesIO(image)))
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    return LabelExtraction(
        label_type="unknown",
        # The longest line on packaging is usually the ingredient list, and
        # the name is usually short and near the top — but "usually" is not
        # good enough to fill a field with, so neither is guessed.
        name=None,
        ingredients=_coerce_ingredients(", ".join(lines)),
        confidence=0.0,
        warnings=[
            "Read without an AI vision model, so nothing was parsed into "
            "fields — the text below is raw and almost certainly needs "
            "editing. Configure OCR_API_KEY for a much better read.",
        ],
        provider="tesseract",
        model="tesseract",
    )


def _empty_extraction(reason: str) -> LabelExtraction:
    """No read was possible. The user can still type the details in."""
    return LabelExtraction(
        confidence=0.0,
        warnings=[reason],
        provider="none",
        model="none",
    )


async def extract_label(image: bytes, mime: str = "image/jpeg") -> LabelExtraction:
    """
    Read a product label from an image.

    Falls back the same way the AI gateway does, and for the same reason: the
    whole vision chain is tried before anything weaker. Only when every
    vision-capable provider has failed does it drop to tesseract, and then to
    an empty form the user fills in by hand. A scan never dead-ends.
    """
    from app.ai.providers import AllProvidersFailed, resolve_chain

    provider = resolve_provider()

    # OCR_PROVIDER=tesseract is an explicit instruction to stay local; anything
    # else means "read it with a model if you can".
    if provider != "tesseract" and resolve_chain(vision=True):
        try:
            return await _extract_with_vision(image, mime, provider)
        except AllProvidersFailed as e:
            print(f"⚠️ Label reading failed on every provider: {e}")
        except Exception as e:  # noqa: BLE001 — provider SDKs raise many types
            print(f"⚠️ Label reading failed: {e}")
        provider = "tesseract"
    elif provider != "tesseract":
        # A model was asked for but none is usable — say so rather than
        # silently producing a worse read.
        print("⚠️ No vision-capable provider is configured; falling back to tesseract.")
        provider = "tesseract"

    if provider == "tesseract":
        try:
            return _extract_with_tesseract(image)
        except Exception as e:  # noqa: BLE001 — missing binary, decode failure
            print(f"⚠️ Tesseract OCR failed: {e}")
            return _empty_extraction(
                "We couldn't read this label automatically. "
                "Please enter the details below."
            )

    return _empty_extraction(
        f"Unknown OCR provider '{provider}'. Please enter the details below."
    )
