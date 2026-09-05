"""
Importing products from Open Food Facts.

Two routes now reach this: a barcode scan of a product we don't hold, and a
user picking an Open Food Facts candidate out of a name search. They must
create identical rows — a product's data quality cannot depend on which screen
found it — so the import lives here rather than inline in either route.

Everything imported is written as tier 2 (verified external database) and
UNVERIFIED, matching what the barcode scan has always done.
"""

from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.products.models import (
    DataQuality, DataSourceTier, NutritionFact, Product, ProductAllergen,
    ProductIdentifier, ProductIngredient, VerificationStatus,
)

OFF_SOURCE = "open_food_facts"


async def find_local_by_barcode(db: AsyncSession, barcode: str) -> Optional[Product]:
    """
    Find a product we already hold for this barcode.

    Checks the barcode index first, then the external reference. Both are
    needed: a product imported from a *name* search may have arrived before we
    ever saw its barcode, and one submitted by a user carries a barcode but no
    external id.

    identifier_value is unique (migration c9e1a7f3d520) but the lookup still
    takes the earliest row explicitly, so a pre-constraint database with
    duplicates degrades to a stale answer rather than a 500.
    """
    identifier = (await db.execute(
        select(ProductIdentifier)
        .where(ProductIdentifier.identifier_value == barcode)
        .order_by(ProductIdentifier.id)
        .limit(1)
    )).scalars().first()

    if identifier:
        product = (await db.execute(
            select(Product).where(Product.id == identifier.product_id)
        )).scalar_one_or_none()
        if product:
            return product

    return (await db.execute(
        select(Product)
        .where(Product.external_source == OFF_SOURCE, Product.external_id == barcode)
        .order_by(Product.id)
        .limit(1)
    )).scalars().first()


async def import_from_openfoodfacts(
    db: AsyncSession,
    barcode: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> Optional[Product]:
    """
    Fetch a product from Open Food Facts by barcode and persist it.

    Returns the existing row if we already hold this product, the newly
    created one on success, or None when Open Food Facts has no such product.

    The caller is responsible for committing — every route here runs inside
    get_db's transaction, which commits on the way out.
    """
    existing = await find_local_by_barcode(db, barcode)
    if existing:
        return existing

    from app.products.openfoodfacts import OFFClient

    off_client = OFFClient()
    try:
        off_data = await off_client.get_product_by_barcode(barcode)
    finally:
        await off_client.close()

    if not off_data:
        return None

    product = Product(
        name=off_data.get("name") or "Unknown Product",
        brand=off_data.get("brand"),
        category=off_data.get("category"),
        image_url=off_data.get("image_url"),
        serving_size=off_data.get("serving_size"),
        verification_status=VerificationStatus.UNVERIFIED,
        data_quality=DataQuality.MEDIUM,
        data_source_tier=DataSourceTier.VERIFIED_DB,
        external_source=off_data.get("external_source") or OFF_SOURCE,
        # OFF omits `code` from some responses; fall back to the barcode we
        # asked for, or the external-reference dedupe index has nothing to key
        # on and the same product can be imported twice.
        external_id=off_data.get("external_id") or barcode,
    )
    db.add(product)
    await db.flush()

    db.add(ProductIdentifier(
        product_id=product.id,
        identifier_type="barcode",
        identifier_value=barcode,
    ))

    if off_data.get("nutrition_facts"):
        db.add(NutritionFact(product_id=product.id, **off_data["nutrition_facts"]))

    for ing_data in off_data.get("ingredients", []):
        db.add(ProductIngredient(
            product_id=product.id,
            name=ing_data["name"],
            position=ing_data["position"],
            percentage=ing_data["percentage"],
        ))

    for allg_data in off_data.get("allergens", []):
        db.add(ProductAllergen(
            product_id=product.id,
            allergen=allg_data["allergen"],
            certainty=allg_data["certainty"],
        ))

    await db.flush()

    # Hidden-allergen pass over the raw ingredient names, same as before.
    if background_tasks is not None:
        from app.products.tasks import analyze_product_allergens_ai
        ingredient_names = [i["name"] for i in off_data.get("ingredients", [])]
        if ingredient_names:
            background_tasks.add_task(
                analyze_product_allergens_ai, product.id, ingredient_names
            )

    return product
