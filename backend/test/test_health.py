"""
Exercise the health-context pipeline.

Run with the API up, from the backend directory:

    python test/test_health.py [base_url]

The parts that must hold no matter which model reads a document:

  1. Analyte normalisation, unit conversion and high/low flagging.
  2. Condition resolution — which patterns a set of readings activates.
  3. Goal-conflict detection — the set intersection between what a goal wants
     raised and what a condition wants lowered.
  4. Scoring: a product that works against a marker scores lower than the same
     product for a user with no health context, deterministically.
  5. The privacy contract: fail-closed encryption, consent gating, ownership
     scoping, and withdrawal actually deleting.

No model is called. The extraction step is exercised through parse_results
with representative model output, so the half that writes to the database and
moves scores is tested for free.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402

from app.health.analytes import (  # noqa: E402
    convert_unit, flag_for, normalize_analyte,
)
from app.health.extraction import parse_results  # noqa: E402
from app.health.profiles import (  # noqa: E402
    detect_goal_conflicts, resolve_conditions,
)
from app.personalization.engine import (  # noqa: E402
    GOAL_PROFILES, calculate_suitability,
)

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000") + "/api/v1"

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  {'✅' if condition else '❌'} {message}")
    if not condition:
        failures.append(message)


def test_analytes() -> None:
    print("\n── Analytes ──")
    for printed, expected in [
        ("HbA1c", "hba1c"),
        ("Glycated Hemoglobin", "hba1c"),
        ("Fasting Blood Sugar", "fasting_glucose"),
        ("LDL Cholesterol", "ldl_cholesterol"),
        ("S. Creatinine", None),          # unknown prefix — not guessed at
        ("Creatinine Serum", "creatinine"),
        ("SGPT", "alt"),
        ("Haemoglobin", "hemoglobin"),
        ("Vitamin D", "vitamin_d"),
        ("Total Protein", None),          # a real test we do not model
    ]:
        got = normalize_analyte(printed)
        check(got == expected, f"'{printed}' → {got}")

    # Unit conversion. A mmol/L glucose scored against a mg/dL threshold is
    # wrong by 18x, which is the difference between normal and alarming.
    value, converted = convert_unit("fasting_glucose", 5.5, "mmol/L")
    check(converted and abs(value - 99.1) < 0.5, f"5.5 mmol/L → {value:.1f} mg/dL")
    value, converted = convert_unit("fasting_glucose", 99, "mg/dL")
    check(not converted and value == 99, "mg/dL left alone")
    value, converted = convert_unit("fasting_glucose", 99, "furlongs")
    check(not converted, "unknown unit reported as unconverted, not guessed")

    print("\n── Flagging ──")
    check(flag_for("hba1c", 6.4) == "high", "HbA1c 6.4 is high")
    check(flag_for("hba1c", 5.2) == "normal", "HbA1c 5.2 is normal")
    check(flag_for("hemoglobin", 10.1) == "low", "Haemoglobin 10.1 is low")
    check(flag_for("ldl_cholesterol", 90) == "normal", "LDL 90 is normal")
    # The report's own range wins over ours — it reflects that lab's assay.
    check(
        flag_for("ldl_cholesterol", 110, ref_low=None, ref_high=130) == "normal",
        "the report's own reference range overrides our default",
    )


def test_extraction_parsing() -> None:
    print("\n── Extraction parsing ──")
    result = parse_results({
        "report_date": "2026-08-14",
        "results": [
            {"label": "HbA1c", "value": "6.4", "unit": "%", "ref_high": 5.7},
            {"label": "Fasting Blood Sugar", "value": 5.9, "unit": "mmol/L"},
            {"label": "LDL Cholesterol", "value": 168, "unit": "mg/dL", "ref_high": 100},
            {"label": "Haemoglobin", "value": 10.2, "unit": "g/dL", "ref_low": 12},
            {"label": "Total Protein", "value": 7.1, "unit": "g/dL"},
            {"label": "Blood Group", "value": "O+", "unit": None},
            {"label": "HbA1c", "value": 6.4, "unit": "%"},
            {"label": "", "value": 1},
        ],
        "warnings": [],
    }, "pdf_text", "test-model")

    labels = [m.label for m in result.markers]
    check("Blood Group" not in labels, "non-numeric result dropped")
    check(labels.count("HbA1c") == 1, "duplicate test listed once")
    check("" not in labels, "unlabelled row dropped")

    by_label = {m.label: m for m in result.markers}
    check(by_label["HbA1c"].flag == "high", "HbA1c 6.4 flagged high")
    check(by_label["LDL Cholesterol"].flag == "high", "LDL 168 flagged high")
    check(by_label["Haemoglobin"].flag == "low", "Haemoglobin 10.2 flagged low")

    glucose = by_label["Fasting Blood Sugar"]
    check(
        glucose.unit_converted and abs(glucose.value - 106.3) < 0.5,
        f"5.9 mmol/L converted to {glucose.value} mg/dL",
    )
    check(glucose.flag == "high", "converted glucose flagged against the right threshold")

    protein = by_label["Total Protein"]
    check(
        not protein.recognized and protein.flag == "unknown",
        "an unmodelled test is kept but never flagged",
    )
    check(
        by_label["HbA1c"].measured_at == "2026-08-14",
        "report date applied to rows with no date of their own",
    )
    check(all(m.to_dict()["confirmed"] is False for m in result.markers),
          "nothing arrives pre-confirmed")


def test_conditions() -> None:
    print("\n── Condition resolution ──")
    markers = [
        {"analyte": "hba1c", "label": "HbA1c", "value": 6.4, "flag": "high",
         "unit": "%", "measured_at": "2026-08-14"},
        {"analyte": "ldl_cholesterol", "label": "LDL", "value": 168, "flag": "high",
         "unit": "mg/dL", "measured_at": "2026-08-14"},
        {"analyte": "hemoglobin", "label": "Hb", "value": 10.2, "flag": "normal",
         "unit": "g/dL", "measured_at": "2026-08-14"},
    ]
    keys = {c["key"] for c in resolve_conditions(markers)}
    check("elevated_glucose" in keys, "high HbA1c activates elevated glucose")
    check("high_ldl_cholesterol" in keys, "high LDL activates elevated LDL")
    check("anemia_risk" not in keys, "a normal-flagged marker activates nothing")

    check(not resolve_conditions([]), "no markers, no conditions")
    check(
        not resolve_conditions([{"analyte": "hba1c", "flag": "normal"}]),
        "normal results activate nothing",
    )

    # A newer normal reading must outrank an older abnormal one.
    superseded = resolve_conditions([
        {"analyte": "hba1c", "flag": "high", "measured_at": "2024-01-01"},
        {"analyte": "hba1c", "flag": "normal", "measured_at": "2026-08-14"},
    ])
    check(not superseded, "the most recent reading per analyte wins")


def test_goal_conflicts() -> None:
    print("\n── Goal conflicts ──")
    kidney = resolve_conditions([{"analyte": "egfr", "flag": "low"}])
    check(len(kidney) == 1, "low eGFR activates reduced kidney function")

    conflicts = detect_goal_conflicts(
        [("muscle gain", GOAL_PROFILES["muscle gain"])], kidney
    )
    check(len(conflicts) == 1, "muscle gain conflicts with reduced kidney function")
    if conflicts:
        check("protein_g" in conflicts[0]["nutrients"],
              f"conflict is on protein -> {conflicts[0]['nutrients']}")
        check("doctor" in conflicts[0]["description"],
              "the conflict text points at a clinician rather than deciding")

    # A goal with no opposing nutrient must not be reported as conflicting.
    glucose = resolve_conditions([{"analyte": "hba1c", "flag": "high"}])
    check(
        not detect_goal_conflicts([("heart health", GOAL_PROFILES["heart health"])], glucose),
        "heart health does not conflict with elevated glucose",
    )
    check(not detect_goal_conflicts([("muscle gain", None)], kidney),
          "an unscoreable goal is skipped, not guessed at")


def test_scoring() -> None:
    print("\n── Scoring ──")
    sugary = {
        "energy_kcal": 480, "protein_g": 4, "total_fat_g": 20,
        "saturated_fat_g": 12, "trans_fat_g": 0.2,
        "total_carbohydrates_g": 65, "total_sugars_g": 48,
        "added_sugars_g": 40, "fiber_g": 1.0, "sodium_mg": 300,
        "cholesterol_mg": 15, "potassium_mg": 200, "iron_mg": 1.0,
    }
    args = dict(
        product_allergens=[], product_ingredients=[{"name": "sugar", "position": 1}],
        user_goals=[], user_allergies=[], user_preferences=[],
    )

    baseline = calculate_suitability(product_nutrition=sugary, **args)
    glucose = resolve_conditions([{"analyte": "hba1c", "flag": "high"}])
    with_health = calculate_suitability(
        product_nutrition=sugary, health_conditions=glucose, **args
    )

    check(
        with_health.overall_score < baseline.overall_score,
        f"a sugary product scores lower with elevated glucose "
        f"({baseline.overall_score} → {with_health.overall_score})",
    )
    check(with_health.breakdown["health_adjustment"] < 0,
          f"health adjustment is negative ({with_health.breakdown['health_adjustment']})")
    check(with_health.breakdown["health_conflict"] in ("watch", "avoid"),
          f"health conflict level reported ({with_health.breakdown['health_conflict']})")

    health_flags = [f for f in with_health.flags if f.category == "health"]
    check(len(health_flags) >= 2, f"health flags raised ({len(health_flags)})")
    check(
        any("not medical advice" in f.description for f in health_flags),
        "the medical disclaimer is attached whenever health context applies",
    )
    # PRD §10 — the platform must not diagnose.
    banned = ("diabetes", "diabetic", "you have", "prediabetes", "disease")
    offenders = [
        f.title for f in health_flags
        if any(b in (f.title + f.description).lower() for b in banned)
    ]
    check(not offenders, f"no diagnostic language in health flags -> {offenders}")

    # Determinism: the same inputs must always give the same number.
    repeat = calculate_suitability(
        product_nutrition=sugary, health_conditions=glucose, **args
    )
    check(repeat.overall_score == with_health.overall_score,
          "the same markers and product always produce the same score")

    # A product that suits the marker must not be penalised.
    wholesome = {
        "energy_kcal": 120, "protein_g": 9, "total_fat_g": 2,
        "saturated_fat_g": 0.4, "total_carbohydrates_g": 14,
        "total_sugars_g": 2, "added_sugars_g": 0, "fiber_g": 8,
        "sodium_mg": 40, "cholesterol_mg": 0,
    }
    good = calculate_suitability(
        product_nutrition=wholesome, health_conditions=glucose, **args
    )
    check(good.breakdown["health_adjustment"] >= 0,
          f"a suitable product is not penalised ({good.breakdown['health_adjustment']})")

    # Health context must never turn into a hard block — that would be the
    # software making a clinical decision.
    check(good.overall_score > 40, f"health context does not veto a food ({good.overall_score})")

    # A product with no nutrition data cannot be judged against markers.
    blank = calculate_suitability(product_nutrition=None, health_conditions=glucose, **args)
    check(blank.breakdown["health_adjustment"] == 0,
          "no nutrition data means no health adjustment")


async def test_api() -> None:
    print("\n── API and privacy contract ──")
    from app.health.crypto import is_available
    if not is_available():
        print("  ⚠️  SKIPPED: HEALTH_ENCRYPTION_KEY is not set in this process.")
        return

    suffix = os.urandom(4).hex()
    async with httpx.AsyncClient(timeout=60.0) as client:
        async def register(tag):
            r = await client.post(f"{BASE}/auth/register", json={
                "email": f"hx{tag}{suffix}@example.com",
                "username": f"hx{tag}{suffix}",
                "password": "TestPass123!",
            })
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        auth = await register("a")
        other = await register("b")

        ctx = (await client.get(f"{BASE}/health/context", headers=auth)).json()
        if not ctx.get("enabled"):
            print(f"  ⚠️  SKIPPED: server has health disabled ({ctx.get('unavailable_reason')})")
            return
        check(ctx["consent_given"] is False, "consent starts off")

        # Upload before consent must be refused.
        r = await client.post(
            f"{BASE}/health/documents", headers=auth,
            files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        check(r.status_code == 403, f"upload without consent refused ({r.status_code})")

        r = await client.put(f"{BASE}/health/consent", headers=auth, json={"consent": True})
        check(r.status_code == 200 and r.json()["consent_given"], "consent recorded")

        # A PDF with no text layer: extraction should decline gracefully and
        # still create a document the user can fill in by hand.
        r = await client.post(
            f"{BASE}/health/documents", headers=auth,
            files={"file": ("report.pdf", b"%PDF-1.4\nnot really a pdf", "application/pdf")},
        )
        check(r.status_code == 200, f"unreadable document still accepted ({r.status_code})")
        if r.status_code != 200:
            print(f"      {r.text[:300]}")
            return
        doc = r.json()["document"]
        check(bool(r.json()["warnings"]), "an unreadable document explains itself")
        check(doc["confirmed"] is False, "a new document is unconfirmed")

        doc_id = doc["id"]

        # Ownership: another user must not see it, and must not be told it exists.
        r = await client.get(f"{BASE}/health/documents/{doc_id}", headers=other)
        check(r.status_code == 404, f"another user's document is a 404, not a 403 ({r.status_code})")

        # Confirm markers by hand.
        r = await client.put(f"{BASE}/health/documents/{doc_id}", headers=auth, json={
            "title": "Annual checkup",
            "markers": [
                {"label": "HbA1c", "value": 6.4, "unit": "%", "ref_high": 5.7, "confirmed": True},
                {"label": "Creatinine", "value": 1.6, "unit": "mg/dL", "confirmed": True},
                {"label": "Total Protein", "value": 7.1, "unit": "g/dL", "confirmed": True},
                {"label": "LDL Cholesterol", "value": 168, "unit": "mg/dL", "confirmed": False},
            ],
        })
        check(r.status_code == 200, f"markers confirmed ({r.status_code})")
        saved = r.json()
        check(saved["confirmed"] is True, "document marked reviewed")
        by_label = {m["label"]: m for m in saved["markers"]}
        check(by_label["HbA1c"]["analyte"] == "hba1c", "analyte derived server-side")
        check(by_label["HbA1c"]["flag"] == "high", "flag derived server-side")
        check(by_label["Total Protein"]["recognized"] is False,
              "an unmodelled test is stored but not recognised")

        ctx = (await client.get(f"{BASE}/health/context", headers=auth)).json()
        keys = {c["key"] for c in ctx["conditions"]}
        check("elevated_glucose" in keys, f"conditions resolved -> {sorted(keys)}")
        check("reduced_kidney_function" in keys, "high creatinine resolved")
        check("high_ldl_cholesterol" not in keys,
              "an UNCONFIRMED marker does not activate a condition")

        for condition in ctx["conditions"]:
            check(bool(condition["triggered_by"]),
                  f"'{condition['label']}' says which reading triggered it")

        # The other user must see nothing of this.
        other_ctx = (await client.get(f"{BASE}/health/context", headers=other)).json()
        check(not other_ctx["conditions"] and not other_ctx["documents"],
              "health context does not leak between users")

        # Withdrawing consent deletes, rather than hides.
        r = await client.put(f"{BASE}/health/consent", headers=auth, json={"consent": False})
        check(r.status_code == 200, "consent withdrawn")
        after = r.json()
        check(after["consent_given"] is False, "consent flag cleared")
        check(not after["documents"], "withdrawing consent deleted the documents")
        r = await client.get(f"{BASE}/health/documents/{doc_id}", headers=auth)
        check(r.status_code == 404, f"the document is really gone ({r.status_code})")


async def main() -> int:
    test_analytes()
    test_extraction_parsing()
    test_conditions()
    test_goal_conflicts()
    test_scoring()
    await test_api()

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ All health-context checks passed.")
    return 0


sys.exit(asyncio.run(main()))
