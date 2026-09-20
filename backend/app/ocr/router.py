"""
Label scanning API routes.

Implements the OCR workflow from PRD §14:

    Image → Preprocessing → OCR → Extraction → Parsing → Normalization
          → USER CONFIRMATION → Analysis

The confirmation step is the reason this is two endpoints rather than one.
Nothing read off a photograph becomes a product on its own: /ocr/label reads
and proposes, /ocr/confirm writes what the user approved. An OCR misread that
silently became an ingredient list would be scored by the allergen engine as
though it came from a label, which is the most damaging failure this system
has.

The identification ladder, cheapest and most reliable first:

  1. Barcode decoded by the client (already handled by /products/scan).
  2. Barcode legible in the photo, checksum-verified → exact product.
  3. Name and brand read off the pack → fuzzy match against the catalogue.
  4. Nothing matched → confirm the extraction into a new tier-4 product.
"""

from typing import Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_json, set_json
from app.core.config import get_settings
from app.core.security import get_current_user
from app.core.uploads import read_upload_capped
from app.db.session import get_db
from app.ocr.gateway import PROMPT_VERSION, extract_label, resolve_model, resolve_provider
from app.ocr.schemas import (
    LabelConfirmRequest, LabelExtraction, LabelExtractionResponse,
)
from app.ocr.storage import ImageRejected, ImageStorageUnavailable, store_image
from app.products.models import (
    DataProvenance, DataQuality, DataSourceTier, NutritionFact, Product,
    ProductIdentifier, ProductImage, ProductIngredient, VerificationStatus,
)
from app.products.schemas import ProductMatchResponse, ProductResponse
from app.products.search import find_probable_duplicates, search_products
from app.users.models import User

router = APIRouter(prefix="/ocr", tags=["Label Scanning"])

settings = get_settings()

# How many catalogue candidates to offer. Enough to contain the right product
# when the read was imperfect, few enough to scan by eye.
MATCH_LIMIT = 5


def _read_cache_key(sha256: str) -> str:
    """
    Cache key for one image's extraction.

    Includes the provider, model and prompt version: a different model reads a
    label differently, and editing the prompt changes what we asked for, so
    neither should serve a verdict reached under the old one.
    """
    provider = resolve_provider()
    return f"ocr:read:{sha256}:{provider}:{resolve_model(provider)}:v{PROMPT_VERSION}"


def _extraction_key(extraction_id: str) -> str:
    return f"ocr:extraction:{extraction_id}"


async def _match_catalogue(db: AsyncSession, extraction: LabelExtraction) -> list:
    """
    Find catalogue products this label might be, using the same fuzzy matcher
    that serves typed searches.

    Sharing the matcher is the point: an OCR'd "Amul Pasteurised Buttor" and a
    typed "amul butter" should resolve to the same product, and they will,
    because neither gets special treatment.
    """
    terms = " ".join(filter(None, [extraction.brand, extraction.name])).strip()
    if not terms:
        return []
    matches, _ = await search_products(db, terms, limit=MATCH_LIMIT)
    return [
        ProductMatchResponse(
            **ProductResponse.model_validate(m.product).model_dump(),
            match_score=round(m.score, 4),
        )
        for m in matches
    ]


async def _resolve_barcode(
    db: AsyncSession,
    barcode: Optional[str],
    background_tasks: BackgroundTasks,
) -> Optional[int]:
    """
    Turn a barcode read off the photo into a product, if we can.

    Only reached for checksum-verified digits (see gateway.valid_gtin), so it
    is safe to import from Open Food Facts on a miss — the same path a scanned
    barcode takes. This is the case where a photo of packaging yields a fully
    populated product with no typing at all.
    """
    if not barcode:
        return None

    from app.products.ingest import import_from_openfoodfacts

    try:
        product = await import_from_openfoodfacts(db, barcode, background_tasks)
    except Exception as e:  # noqa: BLE001 — an upstream failure must not fail the scan
        print(f"⚠️ Barcode lookup from OCR failed for {barcode}: {e}")
        return None
    return product.id if product else None


@router.post("/label", response_model=LabelExtractionResponse)
async def scan_label(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Photo of the product or its label"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Read a product label from an uploaded photo.

    Writes nothing to the catalogue except a product resolved from a
    checksum-verified barcode — everything else comes back as a proposal for
    the user to confirm or correct.
    """
    # Capped while reading: normalize_image() enforces the same limit, but only
    # after the whole body is in memory, which is too late to be a defence.
    raw = await read_upload_capped(
        file,
        settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024,
        too_large_detail=(
            f"That image is larger than the {settings.MAX_UPLOAD_SIZE_MB}MB limit. "
            "Try a photo taken at a lower resolution."
        ),
    )
    try:
        image = store_image(raw, file.content_type)
    except ImageRejected as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except ImageStorageUnavailable as e:
        # Our problem, not the user's photo — say so, and log the real cause
        # rather than putting a server path in the response.
        print(f"⚠️ Label image could not be stored: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Label scanning is temporarily unavailable. Please try again shortly.",
        ) from e

    # The image hash is the extraction id, so the confirmation step can find
    # the stored photo even if the cache has since dropped the extraction.
    extraction_id = image.sha256

    cached = await get_json(_read_cache_key(image.sha256))
    if cached is not None:
        extraction = LabelExtraction(**cached)
    else:
        extraction = await extract_label(image.data, image.mime)
        await set_json(
            _read_cache_key(image.sha256),
            extraction.model_dump(),
            settings.OCR_CACHE_TTL,
        )

    barcode_product_id = await _resolve_barcode(
        db, extraction.barcode, background_tasks
    )
    matches = await _match_catalogue(db, extraction)

    response = LabelExtractionResponse(
        extraction_id=extraction_id,
        image_url=image.url_path,
        extraction=extraction,
        matches=matches,
        barcode_product_id=barcode_product_id,
    )

    # Kept so the confirmation screen survives a reload, and so /ocr/confirm
    # can record what the model originally read as provenance.
    await set_json(
        _extraction_key(extraction_id),
        response.model_dump(),
        settings.OCR_EXTRACTION_TTL,
    )

    return response


@router.get("/label/{extraction_id}", response_model=LabelExtractionResponse)
async def get_extraction(
    extraction_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Re-read a pending extraction.

    Lets the confirmation screen survive a page reload without re-uploading
    the photo or paying for a second model call.
    """
    stored = await get_json(_extraction_key(extraction_id))
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That scan has expired. Please upload the photo again.",
        )
    return LabelExtractionResponse(**stored)


@router.post(
    "/confirm", response_model=ProductResponse, status_code=status.HTTP_201_CREATED
)
async def confirm_label(
    data: LabelConfirmRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a product from a label the user has reviewed.

    Written as tier 4 — user-submitted packaging — and LOW data quality, per
    PRD §21. That matters beyond bookkeeping: this product's ingredient list
    came from a photograph, and the rest of the system needs to be able to
    tell it apart from a manufacturer's own data when it decides how much to
    trust an allergen verdict.
    """
    if data.barcode:
        existing = (await db.execute(
            select(ProductIdentifier)
            .where(ProductIdentifier.identifier_value == data.barcode)
            .limit(1)
        )).scalars().first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"A product with this barcode already exists "
                    f"(id {existing.product_id})."
                ),
            )

    # ── Duplicate guard ──
    # A barcode is checked above, but a label photo usually carries no barcode,
    # so nothing stopped the same product being added twice — least helpfully
    # when someone re-photographs a label because the first attempt looked
    # wrong. Blocked rather than merged: only the person holding the packet can
    # say whether two similar names are really the same product, so they are
    # shown the match and can override.
    if not data.force:
        duplicates = await find_probable_duplicates(db, data.name, data.brand)
        if duplicates:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        "We already have a product with almost this name. Open it "
                        "instead, or confirm this really is a different product."
                    ),
                    "matches": [
                        {
                            "id": m.product.id,
                            "name": m.product.name,
                            "brand": m.product.brand,
                            "image_url": m.product.image_url,
                            "match_score": round(m.score, 4),
                        }
                        for m in duplicates
                    ],
                },
            )

    stored = await get_json(_extraction_key(data.extraction_id))
    original = None
    if stored:
        try:
            original = LabelExtraction(**stored["extraction"])
        except Exception:  # noqa: BLE001 — a stale cache shape must not block a submit
            original = None

    product = Product(
        name=data.name,
        brand=data.brand,
        category=data.category,
        serving_size=data.serving_size,
        verification_status=VerificationStatus.USER_SUBMITTED,
        data_quality=DataQuality.LOW,
        data_source_tier=DataSourceTier.USER_SUBMITTED,
        external_source=f"user:{current_user.id}",
    )
    db.add(product)
    await db.flush()

    if data.barcode:
        db.add(ProductIdentifier(
            product_id=product.id,
            identifier_type="barcode",
            identifier_value=data.barcode,
        ))

    # ── Ingredients ──
    # Same comma-separated, quantity-ordered format /products/submit accepts,
    # because that is how labels print them and how the engine reads them.
    ingredient_names: list[str] = []
    if data.ingredients_text:
        seen: set[str] = set()
        for raw_name in data.ingredients_text.split(","):
            name = raw_name.strip().strip(".").strip()
            if not name or len(name) > 255:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            ingredient_names.append(name)
            db.add(ProductIngredient(
                product_id=product.id,
                name=name,
                position=len(ingredient_names),
            ))

    # ── Nutrition ──
    if data.nutrition and data.nutrition.has_any_value():
        values = data.nutrition.model_dump()
        db.add(NutritionFact(
            product_id=product.id,
            serving_size=data.serving_size,
            # NutritionFact spells fibre differently from the label schema.
            dietary_fiber_g=values.pop("dietary_fiber_g", None),
            source="ocr_user_confirmed",
            **values,
        ))

    # ── The photo it came from ──
    image_url = None
    if data.extraction_id:
        image_url = f"/uploads/{data.extraction_id}.jpg"
        label_type = original.label_type if original else "unknown"
        db.add(ProductImage(
            product_id=product.id,
            image_type=label_type,
            image_url=image_url,
            uploaded_by=current_user.id,
        ))

        # Also use it as the product's picture — but only when the shot is
        # actually of the product. Every list in the UI reads
        # Product.image_url, and nothing reads product_images, so without this
        # a product created from a photo showed a grey placeholder while its
        # own photo sat on disk.
        #
        # Panel shots are excluded deliberately: a close-up of a nutrition
        # table is a poor thumbnail, and it is the commonest kind of label
        # photo — the ingredient list is exactly what people photograph.
        if label_type in ("front", "mixed"):
            product.image_url = image_url

    # ── Provenance ──
    # PRD §21: "the system should retain the origin of every important field".
    # Recorded per field rather than per product, because the user may have
    # corrected some values and accepted others, and a later verification pass
    # needs to know which is which.
    read_by = f"ocr:{original.provider}:{original.model}" if original else "ocr:manual"
    corrected = _corrected_fields(data, original)
    for field_name, value in (
        ("name", data.name),
        ("brand", data.brand),
        ("category", data.category),
        ("serving_size", data.serving_size),
        ("ingredients", ", ".join(ingredient_names) or None),
    ):
        if value is None:
            continue
        db.add(DataProvenance(
            product_id=product.id,
            field_name=field_name,
            field_value=str(value)[:2000],
            source=(
                f"{read_by} (user-corrected)"
                if field_name in corrected else read_by
            ),
            source_tier=DataSourceTier.USER_SUBMITTED,
            source_url=image_url,
            verified=False,
        ))

    await db.flush()

    if ingredient_names:
        # Same hidden-allergen pass every other creation path runs. It matters
        # more here than anywhere: an OCR'd list is the least reliable input
        # the allergen engine ever sees.
        from app.products.tasks import analyze_product_allergens_ai
        background_tasks.add_task(
            analyze_product_allergens_ai, product.id, ingredient_names
        )

    return ProductResponse.model_validate(product)


def _corrected_fields(
    data: LabelConfirmRequest, original: Optional[LabelExtraction]
) -> set[str]:
    """
    Which fields the user changed from what was read.

    Recorded because a corrected field carries more authority than an accepted
    one — a human looked at the package and disagreed with the machine.
    """
    if original is None:
        return set()

    corrected: set[str] = set()
    for field_name, submitted, read in (
        ("name", data.name, original.name),
        ("brand", data.brand, original.brand),
        ("category", data.category, original.category),
        ("serving_size", data.serving_size, original.serving_size),
    ):
        if (submitted or None) != (read or None):
            corrected.add(field_name)

    submitted_ingredients = [
        i.strip().lower()
        for i in (data.ingredients_text or "").split(",")
        if i.strip()
    ]
    if submitted_ingredients != [i.strip().lower() for i in original.ingredients]:
        corrected.add("ingredients")

    return corrected
