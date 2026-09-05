"""
Reading markers out of a health document.

Same discipline as the label reader: the model extracts, it does not conclude.
It returns numbers with units and reference ranges; every judgement made about
those numbers afterwards is table-driven (app.health.profiles).

The order of attempts matters, and the cheapest is also the most accurate:

  1. **PDF text layer.** Most lab reports are generated digitally and carry
     real text. Reading it costs nothing, involves no third party, and is
     exact — no transcription step to get wrong. This is the preferred path
     and the one most documents will take.

  2. **Vision model.** For photographed or scanned reports, where there is no
     text layer to read.

Step 1 still uses a model to *structure* the extracted text into markers,
because lab report layouts are wildly inconsistent (multi-column, nested
panels, footnote reference ranges) and a regex parser for them is a project in
itself. The difference is that it works from exact text rather than from
pixels, which removes the transcription errors that matter most here.

Privacy note: sending a health document to a third-party model is disclosure.
The caller must have explicit consent before reaching this module, and the
text-layer path still transmits the document's text. `settings.HEALTH_*`
documents this; a fully local deployment should point the AI gateway at a
self-hosted model.
"""

import io
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import get_settings
from app.health.analytes import (
    ANALYTES, convert_unit, flag_for, normalize_analyte,
)

settings = get_settings()

# Bump when the prompt changes materially.
PROMPT_VERSION = 1

# Enough of a lab report to cover the panels; guards against sending a
# 200-page PDF to a model.
MAX_TEXT_CHARS = 24000


@dataclass
class ExtractedMarker:
    """One reading, normalised and flagged."""
    analyte: Optional[str]        # our key, or None if unrecognised
    label: str                    # as printed on the report
    value: Optional[float]
    unit: Optional[str]
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    flag: str = "unknown"         # low | normal | high | unknown
    measured_at: Optional[str] = None
    # True when we recognised the analyte and can score against it. False
    # markers are still shown to the user — they are their results — but never
    # influence a product score.
    recognized: bool = False
    unit_converted: bool = False
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "analyte": self.analyte,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "ref_low": self.ref_low,
            "ref_high": self.ref_high,
            "flag": self.flag,
            "measured_at": self.measured_at,
            "recognized": self.recognized,
            "unit_converted": self.unit_converted,
            "note": self.note,
            # Set when the user reviews the document. Nothing unconfirmed
            # reaches the scoring engine.
            "confirmed": False,
        }


@dataclass
class ExtractionResult:
    markers: list[ExtractedMarker] = field(default_factory=list)
    source: str = "manual"            # pdf_text | vision | manual
    model: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    report_date: Optional[str] = None


_PROMPT = """You are reading a laboratory test report.

Extract EVERY numeric test result you can see. Transcribe only what is
printed — never infer, complete, or correct a value, and never add a test that
is not on the report.

For each result give:
  - "label": the test name exactly as printed
  - "value": the numeric result only (no units, no comparison signs)
  - "unit": the unit as printed, e.g. "mg/dL", "%", "g/dL", "mmol/L"
  - "ref_low" / "ref_high": the reference range printed for THAT row, as
    numbers. Use null for an open-ended side ("< 200" has only ref_high).
  - "measured_at": the collection or report date in YYYY-MM-DD, or null

Also give "report_date" (YYYY-MM-DD or null) for the report as a whole.

Rules:
  - Do NOT interpret, diagnose, or comment on any result.
  - Do NOT convert units. Report the unit exactly as printed.
  - If a value is illegible, omit that row entirely rather than guessing.
  - Ignore non-numeric results (e.g. "Negative", "Not detected").
  - Ignore patient name, address, doctor and any other identifying detail —
    do not return them in any field.

Return ONLY JSON of this shape:
{
  "report_date": "YYYY-MM-DD or null",
  "results": [
    {"label": "...", "value": 0, "unit": "...", "ref_low": null,
     "ref_high": null, "measured_at": null}
  ],
  "warnings": ["anything that was unclear"]
}

Return {"report_date": null, "results": [], "warnings": [...]} if this is not
a lab report."""


def extract_pdf_text(data: bytes) -> tuple[str, Optional[str]]:
    """
    Pull the text layer out of a PDF.

    Returns (text, warning). Empty text means a scanned PDF with no text
    layer, which the caller handles by falling back to the vision path.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", "PDF support is not installed on the server."

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # A password-protected report cannot be read, and we will not
            # attempt to guess at one.
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                return "", "This PDF is password-protected. Please upload an unlocked copy."

        parts = []
        for page in reader.pages[:20]:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
    except Exception as e:  # noqa: BLE001 — pypdf raises many types on damaged files
        return "", f"That PDF could not be read ({type(e).__name__})."

    return text[:MAX_TEXT_CHARS], None


def build_marker(raw: dict) -> Optional[ExtractedMarker]:
    """
    Normalise one raw result from the model into a marker.

    Returns None for rows with no usable number — a lab report row without a
    value is a heading or a qualitative result, not a measurement.
    """
    label = str(raw.get("label") or "").strip()
    if not label:
        return None

    value = raw.get("value")
    if isinstance(value, str):
        cleaned = value.strip().lstrip("<>=~").replace(",", "").strip()
        try:
            value = float(cleaned.split()[0]) if cleaned else None
        except (ValueError, IndexError):
            value = None
    if not isinstance(value, (int, float)):
        return None
    value = float(value)

    unit = (str(raw.get("unit")).strip() if raw.get("unit") else None) or None

    def _num(key) -> Optional[float]:
        v = raw.get(key)
        if isinstance(v, str):
            v = v.strip().lstrip("<>=~").replace(",", "")
            try:
                v = float(v) if v else None
            except ValueError:
                return None
        return float(v) if isinstance(v, (int, float)) else None

    ref_low, ref_high = _num("ref_low"), _num("ref_high")
    analyte = normalize_analyte(label)

    marker = ExtractedMarker(
        analyte=analyte,
        label=label,
        value=value,
        unit=unit,
        ref_low=ref_low,
        ref_high=ref_high,
        measured_at=(str(raw.get("measured_at")).strip() if raw.get("measured_at") else None),
        recognized=analyte is not None,
    )

    if analyte:
        # Convert into the unit our thresholds are written in. A glucose in
        # mmol/L compared against a mg/dL threshold is wrong by 18x, which
        # would read as wildly abnormal.
        converted, did_convert = convert_unit(analyte, value, unit)
        if did_convert:
            factor = converted / value if value else 1
            marker.value = round(converted, 3)
            marker.unit = ANALYTES[analyte].unit
            marker.unit_converted = True
            marker.note = f"Converted from {unit} (×{factor:.4g})."
            # The printed range is in the printed unit, so convert it too or
            # drop it rather than compare across units.
            marker.ref_low = round(ref_low * factor, 3) if ref_low is not None else None
            marker.ref_high = round(ref_high * factor, 3) if ref_high is not None else None
        marker.flag = flag_for(analyte, marker.value, marker.ref_low, marker.ref_high)

    return marker


def parse_results(data: dict, source: str, model: Optional[str]) -> ExtractionResult:
    """Turn a model's raw JSON into normalised, flagged markers."""
    result = ExtractionResult(source=source, model=model)

    raw_warnings = data.get("warnings")
    if isinstance(raw_warnings, str):
        result.warnings = [raw_warnings] if raw_warnings.strip() else []
    elif isinstance(raw_warnings, list):
        result.warnings = [str(w).strip() for w in raw_warnings if str(w).strip()]

    report_date = data.get("report_date")
    result.report_date = str(report_date).strip() if report_date else None

    rows = data.get("results")
    if not isinstance(rows, list):
        rows = []

    seen: set[str] = set()
    for raw in rows[: settings.MAX_HEALTH_MARKERS]:
        if not isinstance(raw, dict):
            continue
        marker = build_marker(raw)
        if marker is None:
            continue
        # A report that lists the same test twice (panel plus summary) should
        # not produce two markers pulling the same lever twice.
        key = (marker.analyte or marker.label).lower()
        if key in seen:
            continue
        seen.add(key)
        if marker.measured_at is None:
            marker.measured_at = result.report_date
        result.markers.append(marker)

    recognized = sum(1 for m in result.markers if m.recognized)
    if result.markers and not recognized:
        result.warnings.append(
            "We read values from this document but didn't recognise any of the "
            "test names, so none of them can be applied to product scores."
        )

    return result


async def _call_model(content: list | str, model_hint: Optional[str]) -> tuple[dict, str]:
    """
    Send the document through the provider chain and parse its JSON.

    A lab report is uploaded once and reviewed immediately, so a provider
    outage here is especially visible — the user has already handed over a
    sensitive document and would get an empty form back. The chain means one
    provider being busy no longer wastes that.
    """
    from app.ai.providers import complete_json

    result = await complete_json(
        [
            {
                "role": "system",
                "content": (
                    "You transcribe laboratory reports into JSON. You output "
                    "valid JSON only. You never interpret or diagnose, and you "
                    "never return patient-identifying details."
                ),
            },
            {"role": "user", "content": content},
        ],
        temperature=0.0,
        # A full blood panel is a long list of rows; truncating it silently
        # drops readings off the end of the report.
        max_tokens=6000,
        # An image needs a vision-capable provider; extracted PDF text does
        # not, so the text path can use the whole chain.
        vision=(model_hint == "vision"),
        timeout=settings.OCR_TIMEOUT,
    )
    return result.data, f"{result.provider}:{result.model}"


async def extract_from_document(
    data: bytes,
    content_type: Optional[str],
    filename: Optional[str] = None,
) -> ExtractionResult:
    """
    Read markers from an uploaded lab report.

    Tries the PDF text layer first, then a vision model, and returns an empty
    result with an explanation rather than raising — a failed read should land
    the user on a form they can fill in by hand, not an error page.
    """
    is_pdf = (content_type or "").lower() == "application/pdf" or (
        filename or ""
    ).lower().endswith(".pdf")

    if is_pdf:
        text, warning = extract_pdf_text(data)
        if text:
            try:
                payload, model = await _call_model(
                    f"{_PROMPT}\n\n--- REPORT TEXT ---\n{text}", None
                )
                return parse_results(payload, "pdf_text", model)
            except Exception as e:  # noqa: BLE001 — provider SDKs raise many types
                print(f"⚠️ Health extraction (pdf text) failed: {e}")
                return ExtractionResult(
                    source="manual",
                    warnings=[
                        "We couldn't read this report automatically. "
                        "You can enter the values yourself below."
                    ],
                )
        # No text layer — a scan. Fall through to the vision path.
        if warning:
            return ExtractionResult(source="manual", warnings=[warning])

    # Image, or a PDF that turned out to be a scan.
    if is_pdf:
        return ExtractionResult(
            source="manual",
            warnings=[
                "This PDF has no readable text layer, so it looks like a scan. "
                "Upload a photo or screenshot of the report instead, or enter "
                "the values yourself below."
            ],
        )

    try:
        from app.ocr.storage import ImageRejected, normalize_image
        image, _, _ = normalize_image(data, content_type)
    except ImageRejected as e:
        return ExtractionResult(source="manual", warnings=[str(e)])

    import base64
    data_url = f"data:image/jpeg;base64,{base64.b64encode(image).decode('ascii')}"
    try:
        payload, model = await _call_model(
            [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
            "vision",
        )
        return parse_results(payload, "vision", model)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ Health extraction (vision) failed: {e}")
        return ExtractionResult(
            source="manual",
            warnings=[
                "We couldn't read this report automatically. "
                "You can enter the values yourself below."
            ],
        )
