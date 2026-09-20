"""
Product API routes.
"""

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from typing import Optional
from datetime import datetime, timezone

from app.db.session import get_db
from app.core.security import get_current_user, get_optional_user
from app.users.models import User
from app.products.models import (
    Product, ProductIdentifier, ProductImage, ProductIngredient,
    NutritionFact, ProductAllergen, ProductClaim, SavedProduct,
    VerificationStatus
)
from app.products.schemas import (
    ProductResponse, ProductDetailResponse, ProductSearchResult,
    ProductSubmit, NutritionResponse, IngredientBrief, AllergenResponse,
    ProductMatchResponse, ProductSuggestion, ExternalCandidate,
    ProductImportRequest, ProductImageResponse, SavedProductResponse,
)
from app.core.uploads import read_upload_capped
from app.ocr.storage import (
    ImageRejected, ImageStorageUnavailable, store_image, stored_image_url,
)
from app.products.search import (
    WEAK_MATCH_SCORE, best_score, find_probable_duplicates, normalize_query,
    search_products as match_products, suggest_products,
)

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/suggest", response_model=list[ProductSuggestion])
async def suggest(
    q: str = Query(..., min_length=1, max_length=200, description="Partial product name"),
    limit: int = Query(8, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """
    Typeahead suggestions from the local catalogue.

    Local only and never paginated: this runs while the user is still typing,
    so it must not depend on an external API. Use /products/search for the full
    result page, which does fall back to Open Food Facts.
    """
    matches = await suggest_products(db, q, limit=limit)
    return [
        ProductSuggestion(
            id=m.product.id,
            name=m.product.name,
            brand=m.product.brand,
            category=m.product.category,
            image_url=m.product.image_url,
            match_score=round(m.score, 4),
        )
        for m in matches
    ]


@router.get("/search", response_model=ProductSearchResult)
async def search_products(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    category: Optional[str] = None,
    brand: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    include_external: bool = Query(
        True, description="Fall back to Open Food Facts when local matches are weak"
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Search products by name or brand, fuzzily.

    Matching is trigram-based, so a partial, misspelled or reordered name still
    finds the product — results come back ranked by how well they matched, for
    the user to choose from, rather than filtered to a single answer.

    When the local catalogue has nothing convincing, Open Food Facts is
    searched as well. Those hits are returned as *candidates*: they are not
    written to the database until the user picks one (see /products/import).
    """
    matches, total = await match_products(
        db, q, category=category, brand=brand,
        limit=page_size, offset=(page - 1) * page_size,
    )

    products = [
        ProductMatchResponse(
            **ProductResponse.model_validate(m.product).model_dump(),
            match_score=round(m.score, 4),
        )
        for m in matches
    ]

    # Only widen the search when the local catalogue disappointed, and only on
    # the first page — paging through local results should not re-run an
    # external search that returns the same candidates every time.
    #
    # Skipped entirely when a category or brand filter is set: Open Food Facts
    # is searched by name only, so its candidates would ignore the filter the
    # user applied and appear to contradict it.
    should_search_external = (
        include_external
        and page == 1
        and not category
        and not brand
        and best_score(matches) < WEAK_MATCH_SCORE
    )

    external: list[ExternalCandidate] = []
    if should_search_external:
        external = await _search_external(db, q)

    return ProductSearchResult(
        products=products,
        total=total,
        page=page,
        page_size=page_size,
        external_candidates=external,
        external_searched=should_search_external,
    )


async def _search_external(db: AsyncSession, q: str) -> list[ExternalCandidate]:
    """
    Search Open Food Facts by name, cached, and drop anything already local.

    Cached because the endpoint is slow and rate-limited, and because repeated
    searches for the same term are the common case. Keyed on the normalized
    query so casing and spacing variants share one entry.

    Products we already hold are filtered out — offering to "import" a product
    that is sitting in the results above it is confusing, and the import would
    just return the existing row anyway.
    """
    from app.core.cache import get_json, set_json
    from app.core.config import get_settings
    from app.products.openfoodfacts import OFFClient
    from app.products.ingest import OFF_SOURCE

    settings = get_settings()
    normalized = normalize_query(q).lower()
    cache_key = f"off:search:{normalized}"

    raw = await get_json(cache_key)
    if raw is None:
        client = OFFClient()
        try:
            raw = await client.search_by_name(normalized, limit=10)
        finally:
            await client.close()
        # Cached even when empty: a term with no upstream results is exactly
        # the one a user retries, and it costs a full round trip every time.
        await set_json(cache_key, raw, settings.OPEN_FOOD_FACTS_SEARCH_CACHE_TTL)

    codes = [c["code"] for c in raw if c.get("code")]
    if not codes:
        return []

    # One query for both ways a candidate can already be local: by barcode, or
    # by the external reference it was imported under.
    known_ids = set((await db.execute(
        select(ProductIdentifier.identifier_value)
        .where(ProductIdentifier.identifier_value.in_(codes))
    )).scalars().all())
    known_ids.update((await db.execute(
        select(Product.external_id)
        .where(Product.external_source == OFF_SOURCE, Product.external_id.in_(codes))
    )).scalars().all())

    return [
        ExternalCandidate(
            source=OFF_SOURCE,
            external_id=c["code"],
            name=c["name"],
            brand=c.get("brand"),
            category=c.get("category"),
            image_url=c.get("image_url"),
        )
        for c in raw
        if c.get("code") and c["code"] not in known_ids
    ]


@router.post("/images", response_model=ProductImageResponse)
async def upload_product_image(
    file: UploadFile = File(..., description="Photo of the product or its label"),
    current_user: User = Depends(get_current_user),
):
    """
    Store a photo for a product that is about to be submitted.

    Separate from /products/submit so that endpoint can stay JSON: the
    duplicate guard answers 409 and the user resubmits with `force`, and
    making them re-pick their photo for that second attempt would be poor.

    Goes through the same pipeline as label scanning — decoded, EXIF stripped
    (phone photos carry GPS), downscaled, re-encoded and named by content
    hash. The returned id is what /products/submit accepts.

    The image is not attached to anything yet. An id that is never submitted
    leaves an unreferenced file; because storage is content-addressed, the
    same photo uploaded twice is one file either way.
    """
    # Capped while reading — see app/core/uploads.py for why the length check
    # inside normalize_image() is not enough on its own.
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
        print(f"⚠️ Product image could not be stored: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image uploads are temporarily unavailable. Please try again shortly.",
        ) from e

    return ProductImageResponse(image_id=image.sha256, image_url=image.url_path)


@router.post("/import", response_model=ProductResponse)
async def import_product(
    data: ProductImportRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Import an external search candidate into the catalogue.

    This is the point at which an Open Food Facts hit becomes a real product:
    search itself imports nothing. Idempotent — importing a product we already
    hold returns the existing row rather than creating a duplicate.
    """
    from app.products.ingest import OFF_SOURCE, import_from_openfoodfacts

    if data.source != OFF_SOURCE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown product source '{data.source}'.",
        )

    product = await import_from_openfoodfacts(db, data.external_id, background_tasks)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That product is no longer available from Open Food Facts.",
        )

    return ProductResponse.model_validate(product)


@router.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(
    product_id: int,
    current_user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Get detailed product information.

    Authentication is optional: the product itself is public, and a signed-in
    caller additionally gets `is_saved` so the page can paint the save control
    correctly without a second round trip.
    """
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Load nutrition
    nutrition_result = await db.execute(
        select(NutritionFact).where(NutritionFact.product_id == product_id)
    )
    nutrition = nutrition_result.scalar_one_or_none()

    # Load ingredients
    ingredients_result = await db.execute(
        select(ProductIngredient)
        .where(ProductIngredient.product_id == product_id)
        .order_by(ProductIngredient.position)
    )
    ingredients = ingredients_result.scalars().all()

    # Load allergens
    allergens_result = await db.execute(
        select(ProductAllergen).where(ProductAllergen.product_id == product_id)
    )
    allergens = allergens_result.scalars().all()

    # Load claims
    claims_result = await db.execute(
        select(ProductClaim).where(ProductClaim.product_id == product_id)
    )
    claims = claims_result.scalars().all()

    is_saved = False
    if current_user:
        saved_result = await db.execute(
            select(SavedProduct.id).where(
                SavedProduct.user_id == current_user.id,
                SavedProduct.product_id == product_id,
            )
        )
        is_saved = saved_result.scalar_one_or_none() is not None

    return ProductDetailResponse(
        id=product.id,
        name=product.name,
        brand=product.brand,
        category=product.category,
        country=product.country,
        description=product.description,
        image_url=product.image_url,
        serving_size=product.serving_size,
        verification_status=product.verification_status.value,
        data_quality=product.data_quality.value,
        nutrition=NutritionResponse.model_validate(nutrition) if nutrition else None,
        ingredients=[IngredientBrief.model_validate(i) for i in ingredients],
        allergens=[AllergenResponse.model_validate(a) for a in allergens],
        claims=[c.claim for c in claims],
        is_saved=is_saved,
    )


@router.post("/{product_id}/save", response_model=SavedProductResponse,
             status_code=status.HTTP_201_CREATED)
async def save_product(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Save a product to the user's list.

    Idempotent, and a single upsert rather than read-then-write: saving is a
    toggle, so saving twice is not an error, and two concurrent requests — what
    a double-clicked button actually sends — would both see no row and both
    insert. Only the unique constraint this conflicts on can settle that.

    `saved_at` is left alone on conflict: it records when the user first saved
    the product, and re-saving something already saved is a no-op, not a
    refresh.
    """
    exists = await db.execute(select(Product.id).where(Product.id == product_id))
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Product not found")

    stmt = (
        pg_insert(SavedProduct)
        .values(
            user_id=current_user.id,
            product_id=product_id,
            saved_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(constraint="uq_saved_product_user_product")
    )
    await db.execute(stmt)
    await db.flush()

    # Re-read rather than trusting the insert: on conflict it returned nothing,
    # and the response has to carry the original saved_at either way.
    saved = await db.execute(
        select(SavedProduct.saved_at).where(
            SavedProduct.user_id == current_user.id,
            SavedProduct.product_id == product_id,
        )
    )
    saved_at = saved.scalar_one_or_none()

    product = (
        await db.execute(select(Product).where(Product.id == product_id))
    ).scalar_one()

    return SavedProductResponse(
        id=product.id,
        name=product.name,
        brand=product.brand,
        category=product.category,
        country=product.country,
        description=product.description,
        image_url=product.image_url,
        serving_size=product.serving_size,
        verification_status=product.verification_status.value,
        data_quality=product.data_quality.value,
        saved_at=saved_at,
    )


@router.delete("/{product_id}/save", status_code=status.HTTP_204_NO_CONTENT)
async def unsave_product(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove a product from the user's saved list.

    Unsaving something that is not saved succeeds: the caller asked for it to
    be absent, and it is. Returning 404 there would make an unsave that raced
    with another tab look like a failure.
    """
    await db.execute(
        delete(SavedProduct).where(
            SavedProduct.user_id == current_user.id,
            SavedProduct.product_id == product_id,
        )
    )


@router.post("/scan", response_model=ProductResponse)
async def scan_barcode(
    barcode: str = Query(..., description="Product barcode"),
    background_tasks: BackgroundTasks = None,
    current_user=Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Identify a product by barcode.

    Works anonymously. When a signed-in user scans, we also warm the allergen
    inference cache in the background so the suitability request that follows
    doesn't pay the model latency.
    """
    from app.products.ingest import import_from_openfoodfacts

    def _prime(product_id: int) -> None:
        if current_user is not None and background_tasks is not None:
            from app.allergens.tasks import prime_allergen_inference
            background_tasks.add_task(prime_allergen_inference, current_user.id, product_id)

    # The local lookup and the Open Food Facts import both live in
    # app.products.ingest, shared with /products/import: a product found by
    # scanning and the same product found by name must end up as identical
    # rows, and that only stays true if one piece of code creates both.
    product = await import_from_openfoodfacts(db, barcode, background_tasks)

    if not product:
        raise HTTPException(
            status_code=404,
            detail="Product not found for this barcode. You can submit this product.",
        )

    # A freshly imported product needs the cache warmed just as much as one we
    # already held — arguably more, since nothing has ever scored it.
    _prime(product.id)
    return ProductResponse.model_validate(product)


@router.post("/submit", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def submit_product(
    data: ProductSubmit,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit a new product (user-contributed)."""
    # A barcode already on file means this product exists — point the user at it
    # rather than creating a second product that shadows the first on scan.
    if data.barcode:
        existing = await db.execute(
            select(ProductIdentifier)
            .where(ProductIdentifier.identifier_value == data.barcode)
            .limit(1)
        )
        existing_identifier = existing.scalars().first()
        if existing_identifier:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A product with this barcode already exists (id {existing_identifier.product_id}).",
            )

    # Same guard the label-confirmation path applies: a submission with no
    # barcode had nothing stopping it duplicating a product we already hold.
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

    # Resolve the photo before creating anything, so an image id that has
    # expired or was never uploaded is reported rather than silently dropped
    # after the product already exists.
    image_url = None
    if data.image_id:
        image_url = stored_image_url(data.image_id)
        if image_url is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That image is no longer available. Please upload it again.",
            )

    product = Product(
        name=data.name,
        brand=data.brand,
        category=data.category,
        description=data.description,
        verification_status=VerificationStatus.USER_SUBMITTED,
        external_source=f"user:{current_user.id}",
        # Only a front-of-pack shot is used as the product's picture. A
        # close-up of an ingredients panel is kept below but makes a poor
        # thumbnail, and it is a likely thing to upload here.
        image_url=image_url if data.image_type in ("front", "mixed") else None,
    )
    db.add(product)
    await db.flush()

    if image_url:
        # uploaded_by is recorded on every submitted image: these are shown to
        # other users, so an abusive upload has to be traceable to an account.
        db.add(ProductImage(
            product_id=product.id,
            image_type=data.image_type,
            image_url=image_url,
            uploaded_by=current_user.id,
        ))

    # Add barcode if provided
    if data.barcode:
        identifier = ProductIdentifier(
            product_id=product.id,
            identifier_type="barcode",
            identifier_value=data.barcode,
        )
        db.add(identifier)

    # Persist the ingredient list. Labels are comma-separated and ordered by
    # descending quantity, which is exactly what the scoring engine expects —
    # without this the submitted product has no ingredients to score at all.
    if data.ingredients_text:
        seen: set[str] = set()
        position = 0
        for raw_name in data.ingredients_text.split(","):
            name = raw_name.strip().strip(".").strip()
            if not name or len(name) > 255:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            position += 1
            db.add(ProductIngredient(
                product_id=product.id,
                name=name,
                position=position,
            ))

        if position:
            # Same hidden-allergen pass the barcode-import path runs, so a
            # user-submitted product gets the same allergen coverage.
            from app.products.tasks import analyze_product_allergens_ai
            background_tasks.add_task(
                analyze_product_allergens_ai,
                product.id,
                [n for n in (v.strip() for v in data.ingredients_text.split(",")) if n],
            )

    return ProductResponse.model_validate(product)
