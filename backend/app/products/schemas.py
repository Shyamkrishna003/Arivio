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
    # Content hash returned by POST /products/images. Uploading is a separate
    # step so the submit body stays JSON — which keeps the duplicate-guard
    # retry simple, and means a 409 doesn't make the user pick their photo
    # again.
    image_id: Optional[str] = Field(None, max_length=64)
    # What the photo shows. Only a front-of-pack shot becomes the product's
    # thumbnail; a close-up of an ingredients panel is kept but not displayed
    # as the product picture.
    image_type: str = Field("front", max_length=30)
    # Set once the user has seen the "this looks like an existing product"
    # warning and confirmed it really is a different one.
    force: bool = False


class ProductImageResponse(BaseModel):
    """An uploaded image, before it belongs to any product."""
    image_id: str
    image_url: str


class ProductMatchResponse(ProductResponse):
    """A product plus how well it matched the query."""
    # 0..1, from pg_trgm. Surfaced so the UI can say how sure the match is
    # rather than presenting a fuzzy hit as an exact one (PRD §15).
    match_score: float = 1.0


class ProductSuggestion(BaseModel):
    """
    A typeahead row.

    Deliberately narrower than ProductResponse: this is fetched on every
    keystroke, so it carries only what the dropdown renders.
    """
    id: int
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[str] = None
    match_score: float

    model_config = {"from_attributes": True}


class ExternalCandidate(BaseModel):
    """
    A product found on Open Food Facts that we have NOT imported.

    It has no local id, so it cannot be opened directly — the UI sends the
    source and external_id back to /products/import, and navigates to the
    product that call returns.
    """
    source: str = "open_food_facts"
    external_id: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[str] = None


class ProductImportRequest(BaseModel):
    source: str = Field("open_food_facts", max_length=100)
    external_id: str = Field(..., max_length=255)


class ProductSearchResult(BaseModel):
    products: List[ProductMatchResponse]
    total: int
    page: int
    page_size: int
    # Candidates from Open Food Facts, present only when the local catalogue
    # had nothing convincing. Empty is not the same as "not checked" — hence
    # the flag below, so the UI can distinguish "nothing out there" from "we
    # couldn't reach Open Food Facts".
    external_candidates: List[ExternalCandidate] = []
    external_searched: bool = False
