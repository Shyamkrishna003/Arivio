"""
Label extraction schemas.

Two jobs:

  1. Constrain what the vision model may return. A label read is not free
     text — it becomes a product's ingredient list, which is what the allergen
     engine scores. Anything malformed must fail here, loudly, rather than
     reach the database.

  2. Carry the extraction to the confirmation screen and back. PRD §14
     requires the user to correct OCR errors *before* analysis, so nothing
     extracted is written to a product until it comes back through
     LabelConfirmRequest with the user's edits applied.
"""

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.products.schemas import ProductMatchResponse


class ExtractedNutrition(BaseModel):
    """
    Nutrition read off a panel, per 100g.

    Every field is optional: a panel may be absent, cropped or unreadable, and
    a missing value must stay missing. Guessing one would silently change a
    suitability score.
    """
    energy_kcal: Optional[float] = None
    protein_g: Optional[float] = None
    total_fat_g: Optional[float] = None
    saturated_fat_g: Optional[float] = None
    trans_fat_g: Optional[float] = None
    cholesterol_mg: Optional[float] = None
    total_carbohydrates_g: Optional[float] = None
    total_sugars_g: Optional[float] = None
    added_sugars_g: Optional[float] = None
    dietary_fiber_g: Optional[float] = None
    sodium_mg: Optional[float] = None

    @field_validator("*", mode="before")
    @classmethod
    def clean_value(cls, v, info) -> Optional[float]:
        """
        Parse a nutrient figure, or drop it.

        Runs in "before" mode so it sees whatever the model actually emitted.
        Pydantic's own coercion would raise on `"not a number"`, and a raised
        error here fails the entire extraction — one unreadable figure on a
        panel would throw away an otherwise good read of the whole label, and
        500 the request. Dropping is the right failure: the value becomes "not
        detected" and the confirmation screen asks the user for it.

        Two classes of input are handled:

        Unit-laden strings. Models return "12.5 g", "480 kcal", "1,5" with a
        European decimal comma, or "<0.5" for a trace declaration. The number
        is extracted; "<0.5" is read as 0.5, the conservative direction for a
        figure the label itself declares only as an upper bound.

        Physically impossible values. A misplaced decimal point ("0.5g" →
        "5000g") is the failure mode that matters most: it looks unremarkable
        in a form field but moves a suitability score hard. Nothing per 100g
        can weigh more than 100g, and energy above ~900 kcal/100g exceeds pure
        fat, so both are dropped rather than shown for the user to rubber-stamp.
        """
        if v is None:
            return None

        if isinstance(v, str):
            text = v.strip().lstrip("<>~≈").strip()
            if not text:
                return None
            # A comma with no point is a decimal separator ("1,5" = 1.5);
            # with a point it is a thousands separator ("1,234.5").
            if "," in text and "." not in text:
                text = text.replace(",", ".")
            else:
                text = text.replace(",", "")
            match = re.search(r"-?\d+(?:\.\d+)?", text)
            if not match:
                return None
            v = match.group()

        try:
            value = float(v)
        except (TypeError, ValueError):
            return None

        if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
            return None
        if value < 0:
            return None

        name = info.field_name or ""
        if name == "energy_kcal":
            limit = 900.0
        elif name.endswith("_mg"):
            limit = 100_000.0   # 100 g expressed in milligrams
        else:
            limit = 100.0       # grams per 100 g
        return None if value > limit else value

    def has_any_value(self) -> bool:
        return any(v is not None for v in self.model_dump().values())


class LabelExtraction(BaseModel):
    """What a vision model or OCR pass read off one image."""

    # What the image actually showed. The model is asked to say so, because a
    # front-of-pack shot yields a name and no panel, while a back-of-pack shot
    # yields the opposite — and the UI prompts for the missing half.
    label_type: str = "unknown"   # front | ingredients | nutrition | mixed | unknown

    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    serving_size: Optional[str] = None
    # Only when legible in the image. A barcode read this way is a lookup hint,
    # never trusted as an identifier — the client decodes barcodes properly.
    barcode: Optional[str] = None

    ingredients: List[str] = []
    nutrition: ExtractedNutrition = Field(default_factory=ExtractedNutrition)

    # 0..1, the model's own confidence that it read the label correctly.
    # Surfaced to the user (PRD §15) rather than used to gate anything: a
    # confident misread and a hesitant correct read look identical from here.
    confidence: float = 0.0
    # Anything the model or the pipeline wants the user to check.
    warnings: List[str] = []

    provider: str = "none"
    model: str = "none"


class LabelExtractionResponse(BaseModel):
    """
    An extraction, plus what it might already be.

    `matches` is the point of the whole flow: an OCR'd name is run through the
    same fuzzy matcher as a typed one, so a label we already hold resolves to
    the existing product instead of creating a near-duplicate.
    """
    extraction_id: str
    image_url: Optional[str] = None
    extraction: LabelExtraction
    matches: List[ProductMatchResponse] = []
    # True when the barcode read off the image resolved to a product we hold —
    # the UI can then skip confirmation entirely and open it.
    barcode_product_id: Optional[int] = None


class LabelConfirmRequest(BaseModel):
    """
    The extraction as the user corrected it.

    Field-for-field what the confirmation screen shows. The extraction_id ties
    it back to the stored image so the created product keeps the photo it came
    from, and so provenance can record which read produced each value.
    """
    extraction_id: str
    name: str = Field(..., min_length=1, max_length=500)
    brand: Optional[str] = Field(None, max_length=255)
    category: Optional[str] = Field(None, max_length=255)
    serving_size: Optional[str] = Field(None, max_length=100)
    barcode: Optional[str] = Field(None, max_length=255)
    # Comma-separated and ordered by descending quantity, exactly as a label
    # prints it — the same format /products/submit accepts and the scoring
    # engine expects.
    ingredients_text: Optional[str] = None
    nutrition: Optional[ExtractedNutrition] = None
    # Set once the user has seen the "this looks like an existing product"
    # warning and said it really is a different one. Defaulting to False means
    # the check cannot be skipped by accident — only deliberately.
    force: bool = False

    @field_validator("name", "brand", "category", "serving_size", "barcode")
    @classmethod
    def strip_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None
