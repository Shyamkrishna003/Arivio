"""
Product API routes.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from typing import Optional

from app.db.session import get_db
from app.core.security import get_current_user, get_optional_user
from app.users.models import User
from app.products.models import (
    Product, ProductIdentifier, ProductIngredient,
    NutritionFact, ProductAllergen, ProductClaim,
    VerificationStatus
)
from app.products.schemas import (
    ProductResponse, ProductDetailResponse, ProductSearchResult,
    ProductSubmit, NutritionResponse, IngredientBrief, AllergenResponse,
)

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/search", response_model=ProductSearchResult)
async def search_products(
    q: str = Query(..., min_length=1, description="Search query"),
    category: Optional[str] = None,
    brand: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Search products by name, brand, or category."""
    query = select(Product).where(
        or_(
            Product.name.ilike(f"%{q}%"),
            Product.brand.ilike(f"%{q}%"),
        )
    )

    if category:
        query = query.where(Product.category == category)
    if brand:
        query = query.where(Product.brand.ilike(f"%{brand}%"))

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Paginate
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    products = result.scalars().all()

    return ProductSearchResult(
        products=[ProductResponse.model_validate(p) for p in products],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{product_id}", response_model=ProductDetailResponse)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    """Get detailed product information."""
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
    def _prime(product_id: int) -> None:
        if current_user is not None and background_tasks is not None:
            from app.allergens.tasks import prime_allergen_inference
            background_tasks.add_task(prime_allergen_inference, current_user.id, product_id)
    # identifier_value has no unique constraint, and two routes can create a
    # second row for the same barcode: a user submitting a product with a
    # barcode we already hold, and two concurrent scans of an unknown barcode
    # both importing it. Take the earliest match instead of asserting there is
    # exactly one — scalar_one_or_none() would raise here and 500 every future
    # scan of that barcode.
    result = await db.execute(
        select(ProductIdentifier)
        .where(ProductIdentifier.identifier_value == barcode)
        .order_by(ProductIdentifier.id)
        .limit(1)
    )
    identifier = result.scalars().first()

    if identifier:
        product_result = await db.execute(
            select(Product).where(Product.id == identifier.product_id)
        )
        product = product_result.scalar_one_or_none()
        if product:
            _prime(product.id)
            return ProductResponse.model_validate(product)

    # Not found locally, check Open Food Facts
    from app.products.openfoodfacts import OFFClient
    from app.products.models import DataQuality, DataSourceTier
    
    off_client = OFFClient()
    off_data = await off_client.get_product_by_barcode(barcode)
    await off_client.close()
    
    if off_data:
        # Create product from OFF data
        product = Product(
            name=off_data.get("name") or "Unknown Product",
            brand=off_data.get("brand"),
            category=off_data.get("category"),
            image_url=off_data.get("image_url"),
            serving_size=off_data.get("serving_size"),
            verification_status=VerificationStatus.UNVERIFIED,
            data_quality=DataQuality.MEDIUM,
            data_source_tier=DataSourceTier.VERIFIED_DB,
            external_source=off_data.get("external_source"),
            external_id=off_data.get("external_id")
        )
        db.add(product)
        await db.flush()
        
        identifier = ProductIdentifier(
            product_id=product.id,
            identifier_type="barcode",
            identifier_value=barcode
        )
        db.add(identifier)
        
        # Add nutrition facts
        if off_data.get("nutrition_facts"):
            nutrition = NutritionFact(
                product_id=product.id,
                **off_data["nutrition_facts"]
            )
            db.add(nutrition)
            
        # Add ingredients
        for ing_data in off_data.get("ingredients", []):
            ing = ProductIngredient(
                product_id=product.id,
                name=ing_data["name"],
                position=ing_data["position"],
                percentage=ing_data["percentage"]
            )
            db.add(ing)
            
        # Add allergens
        for allg_data in off_data.get("allergens", []):
            allg = ProductAllergen(
                product_id=product.id,
                allergen=allg_data["allergen"],
                certainty=allg_data["certainty"]
            )
            db.add(allg)
            
        await db.flush()
        
        # Dispatch AI background task for hidden allergen detection
        if background_tasks:
            from app.products.tasks import analyze_product_allergens_ai
            ingredient_names = [ing_data["name"] for ing_data in off_data.get("ingredients", [])]
            background_tasks.add_task(analyze_product_allergens_ai, product.id, ingredient_names)

        # A freshly imported product needs the cache warmed just as much as one
        # we already held — arguably more, since nothing has ever scored it.
        _prime(product.id)

        return ProductResponse.model_validate(product)

    raise HTTPException(
        status_code=404,
        detail="Product not found for this barcode. You can submit this product.",
    )


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

    product = Product(
        name=data.name,
        brand=data.brand,
        category=data.category,
        description=data.description,
        verification_status=VerificationStatus.USER_SUBMITTED,
        external_source=f"user:{current_user.id}",
    )
    db.add(product)
    await db.flush()

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
