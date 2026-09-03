"""
Integration with Open Food Facts API for product data ingestion.
"""

import httpx
from typing import Optional, Dict, Any, List
from pydantic import BaseModel
from app.core.config import get_settings

settings = get_settings()

class OFFProduct(BaseModel):
    code: str
    product_name: Optional[str] = None
    brands: Optional[str] = None
    categories: Optional[str] = None
    image_url: Optional[str] = None
    ingredients_text: Optional[str] = None
    nutriments: Dict[str, Any] = {}
    allergens: Optional[str] = None
    serving_size: Optional[str] = None

class OFFClient:
    def __init__(self):
        self.base_url = settings.OPEN_FOOD_FACTS_API_URL
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def get_product_by_barcode(self, barcode: str) -> Optional[Dict[str, Any]]:
        """Fetch product data from Open Food Facts by barcode."""
        try:
            response = await self.client.get(f"/product/{barcode}.json")
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") == 1 and "product" in data:
                return self._normalize_product(data["product"])
            return None
        except httpx.HTTPError:
            return None
            
    def _normalize_product(self, raw_product: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize OFF product data to our internal representation."""
        nutriments = raw_product.get("nutriments", {})
        
        normalized_nutrition = {
            "energy_kcal": nutriments.get("energy-kcal_100g"),
            "total_fat_g": nutriments.get("fat_100g"),
            "saturated_fat_g": nutriments.get("saturated-fat_100g"),
            "trans_fat_g": nutriments.get("trans-fat_100g"),
            "cholesterol_mg": nutriments.get("cholesterol_100g", 0) * 1000 if nutriments.get("cholesterol_100g") else None,
            "sodium_mg": nutriments.get("sodium_100g", 0) * 1000 if nutriments.get("sodium_100g") else None,
            "total_carbohydrates_g": nutriments.get("carbohydrates_100g"),
            "dietary_fiber_g": nutriments.get("fiber_100g"),
            "total_sugars_g": nutriments.get("sugars_100g"),
            "protein_g": nutriments.get("proteins_100g"),
            "serving_size": raw_product.get("serving_size"),
            "serving_unit": "g", # Defaulting to grams/ml typically used by OFF
        }
        
        # Clean up empty values
        normalized_nutrition = {k: v for k, v in normalized_nutrition.items() if v is not None}
        
        # Parse ingredients
        ingredients = []
        raw_ingredients = raw_product.get("ingredients", [])
        for i, ing in enumerate(raw_ingredients):
            ingredients.append({
                "name": ing.get("text", "").strip(),
                "position": i + 1,
                "percentage": ing.get("percent")
            })

        # Parse allergens
        allergens = []
        raw_allergens_str = raw_product.get("allergens", "")
        if raw_allergens_str:
            allergen_list = [a.replace("en:", "").strip() for a in raw_allergens_str.split(",")]
            allergens = [{"allergen": a, "certainty": "declared"} for a in allergen_list if a]

        return {
            "name": raw_product.get("product_name"),
            "brand": raw_product.get("brands", "").split(",")[0] if raw_product.get("brands") else None,
            "category": raw_product.get("categories", "").split(",")[0] if raw_product.get("categories") else None,
            "image_url": raw_product.get("image_url"),
            "serving_size": raw_product.get("serving_size"),
            "ingredients_text": raw_product.get("ingredients_text"),
            "nutrition_facts": normalized_nutrition,
            "ingredients": ingredients,
            "allergens": allergens,
            "external_source": "open_food_facts",
            "external_id": raw_product.get("code")
        }

    async def close(self):
        await self.client.aclose()
