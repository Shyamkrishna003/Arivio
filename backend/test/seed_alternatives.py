import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal
from app.products.models import Product, NutritionFact, ProductIngredient, ProductAllergen
from app.products.models import VerificationStatus, DataQuality, DataSourceTier
import app.community.models  # For SQLAlchemy relationships

async def seed_alternatives():
    async with AsyncSessionLocal() as db:
        # Find an existing product to act as the "bad" product
        result = await db.execute(select(Product).limit(1))
        existing_product = result.scalar_one_or_none()
        
        if not existing_product:
            print("No existing products found. Please scan or search for at least one product first.")
            return

        category = existing_product.category or "Snacks"
        
        print(f"Using category: {category} based on product ID {existing_product.id}")

        # Create 3 healthy alternatives in the same category
        alts_data = [
            {
                "name": "UltraFit Protein Bar",
                "brand": "Optimum Nutrition",
                "protein_g": 22.0,
                "sugar_g": 1.0,
                "fat_g": 6.0,
                "energy": 180,
                "fiber": 10.0
            },
            {
                "name": "Lean Green Super Snack",
                "brand": "Nature's Best",
                "protein_g": 15.0,
                "sugar_g": 2.0,
                "fat_g": 8.0,
                "energy": 150,
                "fiber": 8.0
            },
            {
                "name": "Clean Crunch Cereal",
                "brand": "Healthy Eats",
                "protein_g": 12.0,
                "sugar_g": 3.0,
                "fat_g": 4.0,
                "energy": 120,
                "fiber": 6.0
            }
        ]

        for i, alt in enumerate(alts_data):
            p = Product(
                name=alt["name"],
                brand=alt["brand"],
                category=category,
                serving_size="1 bar (60g)",
                verification_status=VerificationStatus.VERIFIED,
                data_quality=DataQuality.HIGH,
                data_source_tier=DataSourceTier.VERIFIED_DB
            )
            db.add(p)
            await db.flush()

            nutr = NutritionFact(
                product_id=p.id,
                energy_kcal=alt["energy"],
                protein_g=alt["protein_g"],
                total_sugars_g=alt["sugar_g"],
                total_fat_g=alt["fat_g"],
                saturated_fat_g=1.0,
                dietary_fiber_g=alt["fiber"],
                sodium_mg=100.0,
                cholesterol_mg=5.0,
                total_carbohydrates_g=20.0
            )
            db.add(nutr)
            
            # Simple ingredient
            ing = ProductIngredient(
                product_id=p.id,
                name="Whey Protein",
                position=1,
                percentage=50.0
            )
            db.add(ing)

        await db.commit()
        print("Successfully seeded 3 highly suitable alternatives into the database!")

if __name__ == "__main__":
    asyncio.run(seed_alternatives())
