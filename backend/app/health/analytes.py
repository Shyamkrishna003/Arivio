"""
Blood-test analytes we understand.

The extraction model reads whatever a lab printed; this table decides which of
those readings the system is willing to act on, and in what units. Anything
not listed here is kept and shown to the user but never drives a score — the
alternative is letting a model's guess about an unfamiliar analyte quietly
change a suitability number.

Reference ranges are the conventional adult ranges printed on most lab
reports. They exist to render "high"/"low" when the report's own range is
missing or unreadable; the report's own range always wins when present,
because it reflects that lab's assay.

None of this diagnoses anything. A flag here says "outside the usual range",
which is what the paper says too.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Analyte:
    key: str
    label: str
    unit: str
    # Conventional adult reference interval, used only when the report does
    # not carry its own. None on either side means "unbounded in that
    # direction" — a low ferritin matters, a high one is not our business.
    ref_low: Optional[float]
    ref_high: Optional[float]
    # Alternative unit accepted from reports, with a factor to convert INTO
    # `unit`. Indian and US labs differ on several of these, and a
    # mmol/L glucose read as mg/dL is off by 18x.
    alt_units: dict


ANALYTES: dict[str, Analyte] = {
    "hba1c": Analyte("hba1c", "HbA1c", "%", None, 5.7, {}),
    "fasting_glucose": Analyte(
        "fasting_glucose", "Fasting glucose", "mg/dL", 70, 100,
        {"mmol/l": 18.0182},
    ),
    "random_glucose": Analyte(
        "random_glucose", "Random glucose", "mg/dL", None, 140,
        {"mmol/l": 18.0182},
    ),
    "total_cholesterol": Analyte(
        "total_cholesterol", "Total cholesterol", "mg/dL", None, 200,
        {"mmol/l": 38.67},
    ),
    "ldl_cholesterol": Analyte(
        "ldl_cholesterol", "LDL cholesterol", "mg/dL", None, 100,
        {"mmol/l": 38.67},
    ),
    "hdl_cholesterol": Analyte(
        "hdl_cholesterol", "HDL cholesterol", "mg/dL", 40, None,
        {"mmol/l": 38.67},
    ),
    "triglycerides": Analyte(
        "triglycerides", "Triglycerides", "mg/dL", None, 150,
        {"mmol/l": 88.57},
    ),
    "creatinine": Analyte(
        "creatinine", "Creatinine", "mg/dL", 0.6, 1.3,
        {"umol/l": 0.0113, "µmol/l": 0.0113},
    ),
    "egfr": Analyte("egfr", "eGFR", "mL/min/1.73m²", 60, None, {}),
    "uric_acid": Analyte(
        "uric_acid", "Uric acid", "mg/dL", None, 7.0,
        {"umol/l": 0.0168, "µmol/l": 0.0168},
    ),
    "hemoglobin": Analyte(
        "hemoglobin", "Haemoglobin", "g/dL", 12.0, None,
        {"g/l": 0.1},
    ),
    "ferritin": Analyte("ferritin", "Ferritin", "ng/mL", 15, None, {}),
    "vitamin_d": Analyte(
        "vitamin_d", "Vitamin D (25-OH)", "ng/mL", 20, None,
        {"nmol/l": 0.4006},
    ),
    "vitamin_b12": Analyte("vitamin_b12", "Vitamin B12", "pg/mL", 200, None, {}),
    "tsh": Analyte("tsh", "TSH", "mIU/L", 0.4, 4.0, {}),
    "alt": Analyte("alt", "ALT (SGPT)", "U/L", None, 40, {}),
    "ast": Analyte("ast", "AST (SGOT)", "U/L", None, 40, {}),
    "serum_sodium": Analyte("serum_sodium", "Sodium (serum)", "mmol/L", 135, 145, {}),
    "serum_potassium": Analyte("serum_potassium", "Potassium (serum)", "mmol/L", 3.5, 5.1, {}),
}

# What labs actually print, mapped to our keys. Lower-cased and stripped of
# punctuation before lookup (see normalize_analyte).
ANALYTE_ALIASES: dict[str, str] = {
    "hba1c": "hba1c",
    "hb a1c": "hba1c",
    "glycated hemoglobin": "hba1c",
    "glycosylated hemoglobin": "hba1c",
    "a1c": "hba1c",

    "fasting blood sugar": "fasting_glucose",
    "fasting blood glucose": "fasting_glucose",
    "fbs": "fasting_glucose",
    "glucose fasting": "fasting_glucose",
    "fasting plasma glucose": "fasting_glucose",

    "random blood sugar": "random_glucose",
    "rbs": "random_glucose",
    "postprandial glucose": "random_glucose",
    "pp glucose": "random_glucose",

    "cholesterol total": "total_cholesterol",
    "total cholesterol": "total_cholesterol",
    "serum cholesterol": "total_cholesterol",

    "ldl": "ldl_cholesterol",
    "ldl cholesterol": "ldl_cholesterol",
    "low density lipoprotein": "ldl_cholesterol",

    "hdl": "hdl_cholesterol",
    "hdl cholesterol": "hdl_cholesterol",
    "high density lipoprotein": "hdl_cholesterol",

    "triglycerides": "triglycerides",
    "tg": "triglycerides",
    "serum triglycerides": "triglycerides",

    "creatinine": "creatinine",
    "serum creatinine": "creatinine",

    "egfr": "egfr",
    "estimated gfr": "egfr",
    "gfr": "egfr",

    "uric acid": "uric_acid",
    "serum uric acid": "uric_acid",

    "hemoglobin": "hemoglobin",
    "haemoglobin": "hemoglobin",
    "hb": "hemoglobin",
    "hgb": "hemoglobin",

    "ferritin": "ferritin",
    "serum ferritin": "ferritin",

    "vitamin d": "vitamin_d",
    "25 oh vitamin d": "vitamin_d",
    "25 hydroxy vitamin d": "vitamin_d",
    "vitamin d 25 hydroxy": "vitamin_d",

    "vitamin b12": "vitamin_b12",
    "b12": "vitamin_b12",
    "cobalamin": "vitamin_b12",

    "tsh": "tsh",
    "thyroid stimulating hormone": "tsh",

    "alt": "alt",
    "sgpt": "alt",
    "alanine aminotransferase": "alt",

    "ast": "ast",
    "sgot": "ast",
    "aspartate aminotransferase": "ast",

    "sodium": "serum_sodium",
    "serum sodium": "serum_sodium",
    "na": "serum_sodium",

    "potassium": "serum_potassium",
    "serum potassium": "serum_potassium",
    "k": "serum_potassium",
}


def _clean(text: str) -> str:
    """Lower-case and strip punctuation so alias lookup is forgiving."""
    out = []
    for ch in (text or "").lower():
        out.append(ch if ch.isalnum() or ch.isspace() else " ")
    return " ".join("".join(out).split())


def normalize_analyte(name: str) -> Optional[str]:
    """
    Map a printed test name onto one of our analyte keys, or None.

    Exact alias match only — no fuzzy matching. A near-miss here would attach
    a real number to the wrong analyte and score it against the wrong
    thresholds, which is worse than not recognising the test at all.
    """
    cleaned = _clean(name)
    if not cleaned:
        return None
    if cleaned in ANALYTE_ALIASES:
        return ANALYTE_ALIASES[cleaned]
    # Reports often append the specimen or method: "creatinine serum".
    for suffix in (" serum", " plasma", " blood", " level", " test"):
        if cleaned.endswith(suffix):
            trimmed = cleaned[: -len(suffix)].strip()
            if trimmed in ANALYTE_ALIASES:
                return ANALYTE_ALIASES[trimmed]
    return None


def convert_unit(analyte_key: str, value: float, unit: Optional[str]) -> tuple[float, bool]:
    """
    Convert a value into the analyte's canonical unit.

    Returns (value, converted). An unrecognised unit is left alone and
    reported as unconverted, so the caller can warn rather than silently
    compare mmol/L against a mg/dL threshold.
    """
    analyte = ANALYTES.get(analyte_key)
    if not analyte or not unit:
        return value, False

    printed = _clean(unit).replace(" ", "")
    canonical = _clean(analyte.unit).replace(" ", "")
    if printed == canonical:
        return value, False

    for alt, factor in analyte.alt_units.items():
        if printed == _clean(alt).replace(" ", ""):
            return value * factor, True

    return value, False


def flag_for(
    analyte_key: str,
    value: float,
    ref_low: Optional[float] = None,
    ref_high: Optional[float] = None,
) -> str:
    """
    "low" | "normal" | "high" | "unknown".

    The report's own reference range wins when present — it reflects that
    lab's assay and population, which our table cannot.
    """
    analyte = ANALYTES.get(analyte_key)
    low = ref_low if ref_low is not None else (analyte.ref_low if analyte else None)
    high = ref_high if ref_high is not None else (analyte.ref_high if analyte else None)

    if low is None and high is None:
        return "unknown"
    if high is not None and value > high:
        return "high"
    if low is not None and value < low:
        return "low"
    return "normal"
