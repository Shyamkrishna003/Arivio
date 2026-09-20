"""
Exercise the label-scanning pipeline.

Run with the API up (uvicorn on :8000 by default) from the backend directory:

    python test/test_ocr.py [base_url]

Covers the parts that must hold regardless of which vision provider is
configured:

  1. Image handling — EXIF (including GPS) is stripped, orientation applied,
     oversized images downscaled, non-images rejected.
  2. GTIN checksum — the gate that decides whether a barcode read off a photo
     is trusted enough to look a product up with.
  3. Extraction parsing — malformed and hostile model output is coerced or
     dropped, never passed through.
  4. The HTTP flow end to end: upload → extract → match → confirm → product,
     with tier-4 provenance recorded.

The vision model itself is not called. Step 4 seeds the extraction cache with
a known read, so the flow downstream of the model is tested deterministically
and for free — which is the half that writes to the database.
"""

import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.cache import set_json  # noqa: E402
from app.ocr.gateway import parse_extraction, valid_gtin  # noqa: E402
from app.ocr.schemas import LabelExtraction  # noqa: E402
from app.ocr.storage import ImageRejected, normalize_image  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000") + "/api/v1"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  {'✅' if condition else '❌'} {message}")
    if not condition:
        failures.append(message)


def make_image(width=800, height=600, exif: bytes = None, fmt="JPEG") -> bytes:
    """A plain test image, optionally carrying EXIF."""
    img = Image.new("RGB", (width, height), (200, 180, 140))
    buf = io.BytesIO()
    if exif:
        img.save(buf, format=fmt, exif=exif)
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


def make_gps_exif() -> bytes:
    """EXIF carrying GPS coordinates — the tag that must never survive upload."""
    from PIL import Image as PILImage
    exif = PILImage.Exif()
    exif[0x8825] = {                     # GPSInfo
        1: "N", 2: (51.0, 30.0, 0.0),    # latitude
        3: "W", 4: (0.0, 7.0, 0.0),      # longitude
    }
    exif[0x0110] = "NIRNAVI-TEST-CAMERA"  # Model
    exif[0x0112] = 6                     # Orientation: rotate 90°
    return exif.tobytes()


def test_image_handling() -> None:
    print("\n── Image handling ──")

    # GPS in, nothing out.
    raw = make_image(exif=make_gps_exif())
    check(b"NIRNAVI-TEST-CAMERA" in raw, "test image really carries EXIF to begin with")
    clean, w, h = normalize_image(raw, "image/jpeg")
    check(b"NIRNAVI-TEST-CAMERA" not in clean, "EXIF camera model stripped")
    reloaded = Image.open(io.BytesIO(clean))
    check(not reloaded.getexif(), "no EXIF block survives at all (GPS included)")
    # Orientation 6 means the stored image should come out rotated.
    check((w, h) == (600, 800), f"EXIF orientation applied before stripping ({w}x{h})")

    # Downscaling.
    _, w, h = normalize_image(make_image(4000, 3000), "image/jpeg")
    check(max(w, h) == 1600, f"oversized image downscaled to 1600px edge ({w}x{h})")

    # Small images are left alone.
    _, w, h = normalize_image(make_image(400, 300), "image/jpeg")
    check((w, h) == (400, 300), "small image not upscaled")

    # PNG in, JPEG out.
    clean, _, _ = normalize_image(make_image(fmt="PNG"), "image/png")
    check(Image.open(io.BytesIO(clean)).format == "JPEG", "PNG normalised to JPEG")

    # Rejections.
    for label, payload, mime in (
        ("not an image", b"#!/bin/sh\nrm -rf /", "image/jpeg"),
        ("empty upload", b"", "image/jpeg"),
        ("unsupported type", make_image(), "image/heic"),
        ("truncated image", make_image()[:200], "image/jpeg"),
    ):
        try:
            normalize_image(payload, mime)
            check(False, f"{label} rejected")
        except ImageRejected:
            check(True, f"{label} rejected")


def test_gtin() -> None:
    print("\n── Barcode checksum ──")
    # Real GTINs from products already in the catalogue.
    for code in ("3017620422003", "8901719103032", "0051500241776", "80135876"):
        check(valid_gtin(code), f"{code} accepted")
    # A single transposed or wrong digit must fail.
    for code in ("3017620422004", "3017620242003", "12345678", "", "abcdefgh", "301762042200"):
        check(not valid_gtin(code), f"{code or '(empty)'} rejected")


def test_parsing() -> None:
    print("\n── Extraction parsing ──")

    # A well-formed read.
    good = parse_extraction({
        "label_type": "mixed",
        "name": "Test Biscuits", "brand": "TestCo",
        "ingredients": ["wheat flour", "sugar", "palm oil"],
        "nutrition": {"energy_kcal": 480, "total_sugars_g": 22.5, "sodium_mg": 300},
        "confidence": 0.9, "warnings": [],
    }, "test", "test-model")
    check(good.name == "Test Biscuits", "name parsed")
    check(good.ingredients == ["wheat flour", "sugar", "palm oil"], "ingredient order preserved")
    check(good.nutrition.energy_kcal == 480, "nutrition parsed")

    # Malformed shapes models actually emit.
    messy = parse_extraction({
        "label_type": "GARBAGE",
        "name": "  ", "brand": "null",
        "ingredients": "sugar, sugar, palm oil,  , wheat.",
        "nutrition": {"energy_kcal": "not a number", "protein_g": 5, "bogus_key": 1},
        "confidence": 95,
        "warnings": "blurry",
    }, "test", "test-model")
    check(messy.label_type == "unknown", "unknown label_type falls back")
    check(messy.name is None and messy.brand is None, "blank and 'null' become None")
    check(messy.ingredients == ["sugar", "palm oil", "wheat"],
          f"comma string split, deduped, cleaned -> {messy.ingredients}")
    check(messy.nutrition.protein_g == 5, "valid nutrient kept alongside invalid one")
    check(messy.nutrition.energy_kcal is None, "non-numeric nutrient dropped")
    check(messy.confidence == 0.95, "0-100 confidence rescaled to 0-1")
    check(messy.warnings == ["blurry"], "string warning wrapped in a list")

    # Physically impossible values — the misplaced-decimal failure mode.
    absurd = parse_extraction({
        "nutrition": {
            "total_sugars_g": 5000,    # >100g per 100g
            "energy_kcal": 99999,      # beats pure fat
            "protein_g": -3,           # negative
            "sodium_mg": 400,          # legitimate
        },
    }, "test", "test-model")
    check(absurd.nutrition.total_sugars_g is None, "impossible gram value dropped")
    check(absurd.nutrition.energy_kcal is None, "impossible energy dropped")
    check(absurd.nutrition.protein_g is None, "negative value dropped")
    check(absurd.nutrition.sodium_mg == 400, "plausible mg value kept")

    # Unit-laden and localised figures, which models emit constantly.
    units = parse_extraction({
        "nutrition": {
            "total_fat_g": "12.5 g",
            "sodium_mg": "480 mg",
            "total_sugars_g": "1,5",        # European decimal comma
            "dietary_fiber_g": "<0.5",      # trace declaration
            "protein_g": "approx. 7.2g",
            "saturated_fat_g": "",
            "added_sugars_g": "N/A",
        },
    }, "test", "test-model")
    check(units.nutrition.total_fat_g == 12.5, f"'12.5 g' -> {units.nutrition.total_fat_g}")
    check(units.nutrition.sodium_mg == 480, f"'480 mg' -> {units.nutrition.sodium_mg}")
    check(units.nutrition.total_sugars_g == 1.5, f"'1,5' -> {units.nutrition.total_sugars_g}")
    check(units.nutrition.dietary_fiber_g == 0.5, f"'<0.5' -> {units.nutrition.dietary_fiber_g}")
    check(units.nutrition.protein_g == 7.2, f"'approx. 7.2g' -> {units.nutrition.protein_g}")
    check(units.nutrition.saturated_fat_g is None, "empty string dropped")
    check(units.nutrition.added_sugars_g is None, "'N/A' dropped")

    # Barcode gating.
    check(parse_extraction({"barcode": "3017620422003"}, "t", "m").barcode == "3017620422003",
          "checksum-valid barcode kept")
    check(parse_extraction({"barcode": "3017620422004"}, "t", "m").barcode is None,
          "checksum-invalid barcode discarded")

    # An empty response must not explode.
    check(parse_extraction({}, "t", "m").ingredients == [], "empty response handled")

    # Runaway ingredient list is capped.
    flood = parse_extraction({"ingredients": [f"ing{i}" for i in range(500)]}, "t", "m")
    check(len(flood.ingredients) == 120, f"ingredient list capped at 120 ({len(flood.ingredients)})")


def test_provider_resolution() -> None:
    """
    Which provider "auto" picks, and — more importantly — which it refuses to.

    Groq is the case worth locking down: its vision line-up varies by account
    (this project's own Groq account serves none), so auto must never select
    it on the strength of a Groq key alone. It has to fall through to
    tesseract, which works, rather than to a vision model that 404s.
    """
    print("\n── Provider resolution ──")
    from app.core.config import Settings
    import app.ocr.gateway as gw

    original = gw.settings
    try:
        for label, expected, overrides in (
            ("groq key alone falls through to tesseract", "tesseract",
             dict(OCR_PROVIDER="auto", AI_PROVIDER="groq", AI_API_KEY="k")),
            ("gemini key selects gemini", "gemini",
             dict(OCR_PROVIDER="auto", AI_PROVIDER="gemini", AI_API_KEY="k")),
            ("'google' is an alias for gemini", "gemini",
             dict(OCR_PROVIDER="auto", AI_PROVIDER="google", AI_API_KEY="k")),
            ("openai key selects openai", "openai",
             dict(OCR_PROVIDER="auto", AI_PROVIDER="openai", AI_API_KEY="k")),
            ("no key at all uses tesseract", "tesseract",
             dict(OCR_PROVIDER="auto", AI_PROVIDER="groq", AI_API_KEY="")),
            ("a dedicated OCR key overrides the chat provider", "gemini",
             dict(OCR_PROVIDER="gemini", AI_PROVIDER="groq", AI_API_KEY="", OCR_API_KEY="k")),
            ("groq still available when named explicitly", "groq",
             dict(OCR_PROVIDER="groq", AI_PROVIDER="groq", AI_API_KEY="k")),
        ):
            gw.settings = Settings(_env_file=None, **overrides)
            actual = gw.resolve_provider()
            check(actual == expected, f"{label} (got {actual})")
    finally:
        gw.settings = original


async def test_http_flow() -> None:
    print("\n── HTTP flow (upload → match → confirm) ──")

    suffix = os.urandom(4).hex()
    async with httpx.AsyncClient(timeout=60.0) as client:
        reg = await client.post(f"{BASE}/auth/register", json={
            "email": f"ocr{suffix}@example.com",
            "username": f"ocr{suffix}",
            "password": "TestPass123!",
        })
        if reg.status_code != 201:
            check(False, f"could not register a test user: {reg.status_code} {reg.text[:200]}")
            return None, False
        auth = {"Authorization": f"Bearer {reg.json()['access_token']}"}

        image = make_image(exif=make_gps_exif())

        # Seed the extraction cache so the flow below the model is exercised
        # deterministically, with no vision provider and no cost. The key must
        # match what the router computes for these bytes.
        from app.ocr.gateway import PROMPT_VERSION, resolve_model, resolve_provider
        from app.ocr.storage import normalize_image as _norm
        import hashlib

        normalised, _, _ = _norm(image, "image/jpeg")
        sha = hashlib.sha256(normalised).hexdigest()
        provider = resolve_provider()
        key = f"ocr:read:{sha}:{provider}:{resolve_model(provider)}:v{PROMPT_VERSION}"

        seeded = LabelExtraction(
            label_type="mixed",
            # Deliberately a near-miss of a catalogue product, to prove the
            # fuzzy matcher is what resolves it.
            name="dairy milk choclate",
            brand="Cadbury",
            ingredients=["sugar", "cocoa butter", "milk solids"],
            confidence=0.82,
            provider="seeded", model="seeded",
        )
        await set_json(key, seeded.model_dump(), 300)

        r = await client.post(
            f"{BASE}/ocr/label", headers=auth,
            files={"file": ("label.jpg", image, "image/jpeg")},
        )
        if r.status_code != 200:
            check(False, f"POST /ocr/label failed: {r.status_code} {r.text[:300]}")
            return None, False
        body = r.json()
        check(True, "POST /ocr/label accepted the upload")
        check(body["extraction_id"] == sha, "extraction id is the image content hash")
        check(body["image_url"] == f"/uploads/{sha}.jpg", "image stored at a hashed path")

        # The seeded read is only picked up if the SERVER resolves to the same
        # provider and model this process does — the cache key contains both.
        # They differ whenever the server is running with a stale environment,
        # which under Docker is the normal case after editing .env: compose
        # copies the file into the container at creation, and that copy wins
        # over the file afterwards.
        #
        # Detected and reported rather than left to fail three checks with no
        # explanation, because the code under test is fine when it happens.
        seeded_used = body["extraction"]["provider"] == "seeded"
        if not seeded_used:
            served = f"{body['extraction']['provider']}/{body['extraction']['model']}"
            print(
                f"  ⚠️  SKIPPED 3 checks: the server read this image with {served},"
                f"\n      but this process seeded the cache for {provider}/"
                f"{resolve_model(provider)}."
                "\n      The server is running with a different OCR config."
                "\n      Under Docker: sudo docker compose up -d --force-recreate backend"
            )
        else:
            check(body["extraction"]["name"] == "dairy milk choclate",
                  "cached extraction reused")
            names = [m["name"] for m in body["matches"]]
            check(
                any("chocolate" in n.lower() for n in names),
                f"misread name still matched the catalogue -> {names}",
            )

        # Reload survives.
        r2 = await client.get(f"{BASE}/ocr/label/{sha}", headers=auth)
        check(r2.status_code == 200, "GET /ocr/label/{id} rehydrates the extraction")

        # The stored image is served, and is the stripped one.
        base_root = BASE.rsplit("/api/", 1)[0]
        r3 = await client.get(f"{base_root}/uploads/{sha}.jpg")
        check(r3.status_code == 200, "stored image is served")
        check(b"NIRNAVI-TEST-CAMERA" not in r3.content, "served image carries no EXIF")

        # Confirm, with the user correcting the misread name.
        r4 = await client.post(f"{BASE}/ocr/confirm", headers=auth, json={
            "extraction_id": sha,
            "name": f"{suffix} OCR Test Chocolate",
            "brand": "Cadbury",
            "category": "Confectionery",
            "ingredients_text": "sugar, cocoa butter, milk solids",
            "nutrition": {"energy_kcal": 534, "total_sugars_g": 56.0},
        })
        if r4.status_code != 201:
            check(False, f"POST /ocr/confirm failed: {r4.status_code} {r4.text[:300]}")
            return None, False
        product = r4.json()
        check(True, "POST /ocr/confirm created a product")
        check(product["data_quality"] == "low", f"tier-4 data quality is low ({product['data_quality']})")
        check(product["verification_status"] == "user_submitted",
              f"marked user_submitted ({product['verification_status']})")

        detail = (await client.get(f"{BASE}/products/{product['id']}")).json()
        check([i["name"] for i in detail["ingredients"]] == ["sugar", "cocoa butter", "milk solids"],
              "ingredients persisted in label order")
        check(detail["nutrition"] and detail["nutrition"]["energy_kcal"] == 534,
              "nutrition persisted")

        # The duplicate guard: submitting the same label again must be blocked
        # with the existing product offered, and overridable on purpose.
        again = await client.post(f"{BASE}/ocr/confirm", headers=auth, json={
            "extraction_id": sha,
            "name": f"{suffix} OCR Test Chocolate",
            "brand": "Cadbury",
        })
        check(again.status_code == 409,
              f"a repeat submission is blocked as a duplicate ({again.status_code})")
        if again.status_code == 409:
            detail = again.json()["detail"]
            check(bool(detail.get("matches")), "the block names the existing product")

        forced = await client.post(f"{BASE}/ocr/confirm", headers=auth, json={
            "extraction_id": sha,
            "name": f"{suffix} OCR Test Chocolate",
            "brand": "Cadbury",
            "force": True,
        })
        check(forced.status_code == 201,
              f"force=True lets a deliberate near-duplicate through ({forced.status_code})")

        distinct = await client.post(f"{BASE}/ocr/confirm", headers=auth, json={
            "extraction_id": sha,
            "name": f"{suffix} Unrelated Wafer Snack",
        })
        check(distinct.status_code == 201,
              f"a genuinely different name is not blocked ({distinct.status_code})")

        # Rejections.
        r5 = await client.post(
            f"{BASE}/ocr/label", headers=auth,
            files={"file": ("evil.jpg", b"not an image at all", "image/jpeg")},
        )
        check(r5.status_code == 422, f"non-image upload rejected ({r5.status_code})")

        r6 = await client.post(
            f"{BASE}/ocr/label",
            files={"file": ("label.jpg", image, "image/jpeg")},
        )
        check(r6.status_code == 403, f"unauthenticated upload rejected ({r6.status_code})")

        r7 = await client.get(f"{BASE}/ocr/label/deadbeef", headers=auth)
        check(r7.status_code == 404, f"unknown extraction id 404s ({r7.status_code})")

        return product["id"], seeded_used


async def check_provenance(product_id: int, seeded_read_used: bool) -> None:
    print("\n── Provenance (PRD §21) ──")
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    import app.users.models  # noqa: F401
    import app.products.models  # noqa: F401
    import app.ingredients.models  # noqa: F401
    import app.community.models  # noqa: F401
    import app.ai.models  # noqa: F401
    import app.allergens.models  # noqa: F401
    import app.health.models  # noqa: F401
    from app.products.models import DataProvenance, ProductImage

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(DataProvenance).where(DataProvenance.product_id == product_id)
        )).scalars().all()
        fields = {r.field_name for r in rows}
        check("name" in fields and "ingredients" in fields,
              f"origin recorded per field -> {sorted(fields)}")
        check(all(r.source_tier.value == "tier_4_user_submitted" for r in rows),
              "every provenance row is tier 4")
        name_row = next((r for r in rows if r.field_name == "name"), None)
        check(name_row is not None and "user-corrected" in name_row.source,
              f"user's correction recorded -> {name_row.source if name_row else None}")

        # Only meaningful when the seeded read was actually used: it is the
        # thing that supplied a brand for the user to accept unchanged. With a
        # different read the brand was empty, so submitting one IS a correction.
        brand_row = next((r for r in rows if r.field_name == "brand"), None)
        if seeded_read_used:
            check(brand_row is not None and "user-corrected" not in brand_row.source,
                  "an accepted field is not marked corrected")
        else:
            print("  ⚠️  SKIPPED 1 check (accepted-field provenance): needs the seeded read.")

        images = (await db.execute(
            select(ProductImage).where(ProductImage.product_id == product_id)
        )).scalars().all()
        check(len(images) == 1, "the source photo is linked to the product")


async def main() -> int:
    test_image_handling()
    test_gtin()
    test_parsing()
    test_provider_resolution()
    product_id, seeded_read_used = await test_http_flow()
    if product_id:
        await check_provenance(product_id, seeded_read_used)

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ All OCR checks passed.")
    return 0


sys.exit(asyncio.run(main()))
