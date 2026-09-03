"""
Product API schemas.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class ProductSearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    category: Optional[str] = None
    brand: Optional[str] = None
    page: int = 1
    page_size: int = 20


class NutritionResponse(BaseModel):
    energy_kcal: Optional[float] = None
    total_fat_g: Optional[float] = None
    saturated_fat_g: Optional[float] = None
    trans_fat_g: Optional[float] = None
    cholesterol_mg: Optional[float] = None
    sodium_mg: Optional[float] = None
    total_carbohydrates_g: Optional[float] = None
    dietary_fiber_g: Optional[float] = None
    total_sugars_g: Optional[float] = None
    added_sugars_g: Optional[float] = None
    protein_g: Optional[float] = None
    serving_size: Optional[str] = None

    model_config = {"from_attributes": True}


class IngredientBrief(BaseModel):
    id: int
    name: str
    position: Optional[int] = None
    is_allergen: bool = False

    model_config = {"from_attributes": True}


class AllergenResponse(BaseModel):
    allergen: str
    certainty: str

    model_config = {"from_attributes": True}


class ProductResponse(BaseModel):
    id: int
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    country: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    serving_size: Optional[str] = None
    verification_status: str
    data_quality: str

    model_config = {"from_attributes": True}


class ProductDetailResponse(ProductResponse):
    nutrition: Optional[NutritionResponse] = None
    ingredients: List[IngredientBrief] = []
    allergens: List[AllergenResponse] = []
    claims: List[str] = []


class ProductSubmit(BaseModel):
    name: str = Field(..., max_length=500)
    brand: Optional[str] = None
    barcode: Optional[str] = None
    category: Optional[str] = None
    ingredients_text: Optional[str] = None
    description: Optional[str] = None


class ProductSearchResult(BaseModel):
    products: List[ProductResponse]
    total: int
    page: int
    page_size: int
