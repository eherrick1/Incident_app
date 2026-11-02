"""AI extraction: turn an uploaded GL form (PDF/photos/Excel) into the same
structured record the online form produces. Retargeted from the expense app:
same 'render pages as images, ask the vision model for JSON' approach, with
one GL-specific upgrade — Excel uploads of the official Broadspire workbook
are first read deterministically straight from the known cells, and the AI
pass only fills whatever that misses.
"""
import io
import json

import config
import fields as F
from providers import get_provider

PROMPT_TEMPLATE = """You are reading a completed "Broadspire General Liability Reporting Form".
Extract every answer into JSON keyed exactly by the field keys below. Rules:
- Output ONLY a JSON object, no commentary.
- Omit keys the form leaves blank. Do not guess or invent values.
- For yes/no checkboxes use "yes"/"no" (or "unknown" where offered).
- For type "choice" use exactly one of the listed choices.
- Dates as MM/DD/YYYY. Copy narrative text verbatim, do not paraphrase.

Fields:
{schema}
"""


def build_prompt():
    return PROMPT_TEMPLATE.format(schema=json.dumps(F.extraction_schema(), indent=1))


def pdf_to_images(pdf_bytes: bytes, max_pages=4, scale=2.0) -> list[bytes]:
    """Render PDF pages to PNG (the expense app's 'read page as image' approach)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    images = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        images.append(buf.getvalue())
    return images


def extract_from_pdf(pdf_bytes: bytes) -> dict:
    """AI extraction never blocks intake: on any provider failure the record
    comes back empty and the reporter fills the confirm screen manually."""
    try:
        images = pdf_to_images(pdf_bytes)
        if not images:
            return {}
        raw = get_provider().extract(build_prompt(), images)
        return _clean(raw)
    except Exception as exc:
        # Provider errors can echo the prompt or document text back, which for
        # this app means claimant names and injury details. Type only.
        print(f"[extractor] AI extraction failed ({type(exc).__name__})")
        return {}


def extract_from_xlsx(xlsx_bytes: bytes) -> dict:
    """Deterministic read of the official workbook via the fields.py cell map."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), data_only=True)
    ws = wb.active
    out = {}
    for f in F.FIELDS:
        cell = f.get("cell")
        if cell is None:
            continue
        if isinstance(cell, dict):  # checkbox group: whichever option cell has a mark
            for option, coord in cell.items():
                v = ws[coord].value
                if v not in (None, ""):
                    out[f["key"]] = option
                    break
        else:
            v = ws[cell].value
            if v not in (None, ""):
                out[f["key"]] = str(v).strip()
    return _clean(out)


def _clean(raw: dict) -> dict:
    """Keep only known keys; normalize choice values."""
    out = {}
    for key, val in (raw or {}).items():
        f = F.FIELDS_BY_KEY.get(key)
        if f is None or val in (None, ""):
            continue
        sval = str(val).strip()
        if f["type"] in ("yn", "ynu", "choice"):
            sval = sval.lower()
            allowed = f.get("choices") or (["yes", "no"] if f["type"] == "yn"
                                           else ["yes", "no", "unknown"])
            # Single-letter answers as written on the form (Y/N/U, X marks).
            sval = {"y": "yes", "n": "no", "u": "unknown"}.get(sval, sval)
            matches = [c for c in allowed if c.startswith(sval[:3])]
            if not matches:
                continue
            sval = matches[0]
        out[key] = sval
    return out
