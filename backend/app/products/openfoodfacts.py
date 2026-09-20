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

class OFFSearchCandidate(BaseModel):
    """
    A name-search hit that has NOT been imported.

    Search results are shown as candidates and only become a Product row when
    the user picks one. Importing every hit would fill the catalogue with
    dozens of loosely-matched rows per search, which then compete against real
    products in every later local search.
    """
    code: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[str] = None


class OFFClient:
    def __init__(self):
        self.base_url = settings.OPEN_FOOD_FACTS_API_URL
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            headers={"User-Agent": settings.OPEN_FOOD_FACTS_USER_AGENT},
        )

    async def search_by_name(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Search Open Food Facts by product name.

        Returns lightweight candidates, not full products: the caller shows
        them for selection and fetches the full record by barcode only for the
        one the user picks. Requesting just the fields we render keeps the
        response small, which matters because this call is on a user's
        critical path.

        An upstream failure returns an empty list rather than raising — the
        local results are still worth showing.
        """
        params = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": limit,
            "fields": "code,product_name,brands,categories,image_url",
        }
        try:
            # Absolute URL: name search is on the legacy CGI endpoint, not
            # under the versioned API this client's base_url points at.
            response = await self.client.get(
                settings.OPEN_FOOD_FACTS_SEARCH_URL,
                params=params,
                timeout=settings.OPEN_FOOD_FACTS_SEARCH_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as e:
            # Logged, not silent. An upstream block and a genuine zero-result
            # search both produce an empty candidate list, and telling them
            # apart from the outside is impossible — which matters because Open
            # Food Facts rate-limits datacenter IPs, so this can fail in a
            # deployment while working from a laptop.
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(
                f"⚠️ Open Food Facts search failed for {query!r}: "
                f"{type(e).__name__}{f' HTTP {status}' if status else ''}: {e}"
            )
            return []

        candidates: List[Dict[str, Any]] = []
        for raw in data.get("products", []):
            code = str(raw.get("code") or "").strip()
            name = (raw.get("product_name") or "").strip()
            # A hit with no barcode cannot be imported (the import fetches the
            # full record by code), and one with no name cannot be shown.
            if not code or not name:
                continue
            brands = raw.get("brands") or ""
            categories = raw.get("categories") or ""
            candidates.append(OFFSearchCandidate(
                code=code,
                name=name,
                brand=brands.split(",")[0].strip() or None if brands else None,
                category=categories.split(",")[0].strip() or None if categories else None,
                image_url=raw.get("image_url") or None,
            ).model_dump())

        return candidates

    async def get_product_by_barcode(self, barcode: str) -> Optional[Dict[str, Any]]:
        """Fetch product data from Open Food Facts by barcode."""
        try:
            response = await self.client.get(f"/product/{barcode}.json")
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") == 1 and "product" in data:
                return self._normalize_product(data["product"])
            # A reachable API that does not hold this barcode. Distinct from
            # the exception below, and the distinction is the whole point:
            # one means "no such product", the other means "we never asked".
            print(f"ℹ️ Open Food Facts has no product for barcode {barcode}")
            return None
        except httpx.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(
                f"⚠️ Open Food Facts barcode lookup failed for {barcode}: "
                f"{type(e).__name__}{f' HTTP {status}' if status else ''}: {e}"
            )
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
