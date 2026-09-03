"""
Background priming of the allergen inference cache.

Inference runs synchronously inside the suitability endpoint, so the first view
of a product pays the model latency. Scanning happens before the user opens the
detail page, which is a free window to do that work ahead of time — by the time
suitability is requested the verdict is already cached.

Best-effort throughout: this never surfaces an error to the user and never
blocks the scan response.
"""

import traceback

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.personalization.engine import find_unresolved_allergens


async def prime_allergen_inference(user_id: int, product_id: int) -> None:
    """Resolve this user's unresolved allergens against a product, ahead of time."""
    try:
        from app.allergens.inference import resolve_allergens
        from app.products.models import Product, ProductAllergen, ProductIngredient
        from app.users.models import UserAllergy

        async with AsyncSessionLocal() as db:
            product = (await db.execute(
                select(Product).where(Product.id == product_id)
            )).scalar_one_or_none()
            if product is None:
                return

            allergies = [
                {"allergen": a.allergen, "allergy_type": a.allergy_type.value,
                 "severity": a.severity}
                for a in (await db.execute(
                    select(UserAllergy).where(UserAllergy.user_id == user_id)
                )).scalars().all()
            ]
            if not allergies:
                return

            product_allergens = [
                {"allergen": a.allergen, "certainty": a.certainty}
                for a in (await db.execute(
                    select(ProductAllergen).where(ProductAllergen.product_id == product_id)
                )).scalars().all()
            ]
            product_ingredients = [
                {"name": i.name, "position": i.position}
                for i in (await db.execute(
                    select(ProductIngredient)
                    .where(ProductIngredient.product_id == product_id)
                    .order_by(ProductIngredient.position)
                )).scalars().all()
            ]

            unresolved = find_unresolved_allergens(
                allergies, product_allergens, product_ingredients
            )
            if not unresolved:
                return

            await resolve_allergens(
                db=db,
                product_id=product_id,
                product_name=product.name,
                unresolved=unresolved,
                product_ingredients=product_ingredients,
                product_allergens=product_allergens,
            )
            print(f"✅ Primed allergen inference for product {product_id}: {unresolved}")
    except Exception as e:
        print(f"⚠️ Allergen inference priming failed for product {product_id}: {e}")
        traceback.print_exc()
