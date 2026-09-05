"""
Dev helper: preload a label extraction so the confirmation screen can be
reviewed without a vision-model API key.

The scan endpoint caches each read on the image's content hash. This script
computes that hash for a given image and writes a realistic extraction under
it, so uploading that exact file in the UI is a cache hit and the confirmation
screen fills in as though a model had read the label.

Usage, from the backend directory:

    python test/seed_label_scan.py                  # makes a sample label image
    python test/seed_label_scan.py path/to/photo.jpg

Then upload the file it names at http://localhost:5173/scan.

The seeded read deliberately contains a misspelling ("choclate"), so the
catalogue-matching step has something to resolve.
"""

import asyncio
import hashlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image, ImageDraw  # noqa: E402

from app.core.cache import close as close_cache, set_json  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.ocr.gateway import PROMPT_VERSION, resolve_model, resolve_provider  # noqa: E402
from app.ocr.schemas import ExtractedNutrition, LabelExtraction  # noqa: E402
from app.ocr.storage import normalize_image  # noqa: E402

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_label.jpg")


def make_sample_label() -> str:
    """Draw a plausible back-of-pack panel to upload."""
    img = Image.new("RGB", (900, 620), (252, 250, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([30, 30, 870, 590], outline=(120, 120, 120), width=2)
    lines = [
        "CADBURY DAIRY MILK",
        "",
        "INGREDIENTS: Sugar, Cocoa Butter, Milk Solids,",
        "Cocoa Solids, Emulsifiers (442, 476), Flavours.",
        "",
        "NUTRITIONAL INFORMATION (per 100 g)",
        "Energy .................. 534 kcal",
        "Protein ................. 7.3 g",
        "Total Fat ............... 29.8 g",
        "  Saturated Fat ......... 18.5 g",
        "Carbohydrate ............ 57.0 g",
        "  Total Sugars .......... 56.0 g",
        "Sodium .................. 88 mg",
        "",
        "CONTAINS MILK, SOYA.",
    ]
    y = 60
    for line in lines:
        d.text((60, y), line, fill=(20, 20, 20))
        y += 34
    img.save(SAMPLE_PATH, format="JPEG", quality=92)
    return SAMPLE_PATH


async def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else make_sample_label()
    if not os.path.exists(path):
        print(f"No such file: {path}")
        return 1

    with open(path, "rb") as f:
        raw = f.read()

    # Hash the NORMALISED bytes — the same thing the upload endpoint hashes.
    normalised, width, height = normalize_image(raw)
    sha = hashlib.sha256(normalised).hexdigest()

    provider = resolve_provider()
    model = resolve_model(provider)
    key = f"ocr:read:{sha}:{provider}:{model}:v{PROMPT_VERSION}"

    extraction = LabelExtraction(
        label_type="mixed",
        name="dairy milk choclate",   # misspelled on purpose
        brand="Cadbury",
        category="Confectionery",
        serving_size="25 g",
        ingredients=[
            "Sugar", "Cocoa Butter", "Milk Solids", "Cocoa Solids",
            "Emulsifiers (442, 476)", "Flavours",
        ],
        nutrition=ExtractedNutrition(
            energy_kcal=534, protein_g=7.3, total_fat_g=29.8,
            saturated_fat_g=18.5, total_carbohydrates_g=57.0,
            total_sugars_g=56.0, sodium_mg=88,
        ),
        confidence=0.82,
        warnings=["The last line of the ingredient list was partly cut off."],
        provider="seeded", model="seeded-demo",
    )

    await set_json(key, extraction.model_dump(), get_settings().OCR_CACHE_TTL)

    print("\nSeeded a label extraction.\n")
    print(f"  Upload this file:  {os.path.abspath(path)}")
    print(f"  Normalised to:     {width}x{height}, sha256 {sha[:16]}...")
    print(f"  Cache key:         {key}")
    print("\nNow open http://localhost:5173/scan, choose 'Upload Photo',")
    print("and pick that file. You should land on the confirmation screen")
    print("with the fields filled in and 'dairy milk chocolate' offered as")
    print("an existing catalogue match.\n")

    if provider == "tesseract":
        print("Note: OCR_PROVIDER currently resolves to 'tesseract' (no vision")
        print("key configured). That only affects images with no seeded read —")
        print("this one is seeded, so the screen will be fully populated.\n")

    await close_cache()
    return 0


sys.exit(asyncio.run(main()))
