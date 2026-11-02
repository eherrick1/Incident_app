"""Approval outputs, generated from the one approved record:

- filled Broadspire xlsx (values written into the official template's cells)
- clean filled PDF (LibreOffice headless render of that workbook) — the
  official artifact for the carrier
- Word convenience export (python-docx)
- append-only incident register: one row per incident, Excel + Firestore/SQLite
  (reuses the expense app's register mechanism)
"""
import io
from datetime import datetime, timezone

import config
import fields as F
import intake
import storage

# Excel treats a leading =, +, - or @ as the start of a formula. Reporter
# narrative text lands in cells that are e-mailed to the carrier and opened on
# your machine, so "=HYPERLINK(...)" in an accident description would execute
# on click (and DDE payloads can prompt for command execution on older Excel).
# A leading apostrophe forces the cell to text; it is invisible in the rendered
# sheet and in the LibreOffice PDF. CWE-1236.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def excel_safe(value):
    """Return `value` rendered inert for a spreadsheet cell."""
    if not isinstance(value, str):
        return value
    return "'" + value if value[:1] in _FORMULA_LEAD else value


REGISTER_COLUMNS = [
    ("incident_id", "Incident ID"),
    ("approved_at", "Approved (UTC)"),
    ("date_of_accident", "Date of Accident"),
    ("loss_location_name", "Location"),
    ("location_code", "Location Code"),
    ("type_of_loss", "Type of Loss"),
    ("injured_name", "Injured Person"),
    ("fatality", "Fatality"),
    ("removed_via_ambulance", "Ambulance"),
    ("accident_description", "Accident Description (final)"),
    ("reported_by_name", "Reported By"),
    ("intake_method", "Intake"),
    ("approver", "Approved By"),
]


def fill_xlsx(record_fields: dict) -> bytes:
    """Write record values into the official Broadspire workbook template."""
    import openpyxl
    wb = openpyxl.load_workbook(config.BLANK_FORM_XLSX)
    ws = wb.active
    for f in F.FIELDS:
        cell = f.get("cell")
        val = record_fields.get(f["key"])
        if cell is None or val in (None, ""):
            continue
        if isinstance(cell, dict):           # checkbox group -> X in the option cell
            coord = cell.get(str(val).lower())
            if coord:
                ws[coord] = "X"
        else:
            out = str(val)
            if f["type"] in ("yn", "ynu"):    # single-cell Y/N/U answer
                out = {"yes": "Y", "no": "N", "unknown": "U"}.get(out.lower(), out)
            if f["type"] == "money":
                out = out if out.startswith("$") else f"${out}"
            ws[cell] = excel_safe(out)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def fill_pdf(record_fields: dict) -> bytes | None:
    """Official artifact: the filled workbook rendered to PDF via LibreOffice.
    Returns None in local dev if LibreOffice is not installed."""
    return intake.xlsx_to_pdf(fill_xlsx(record_fields))


def fill_docx(doc) -> bytes:
    """Word convenience export of the approved record."""
    from docx import Document
    from docx.shared import Pt

    d = Document()
    d.add_heading("Broadspire General Liability Reporting Form", level=1)
    p = d.add_paragraph(f"Incident {doc['id']} — approved "
                        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    p.runs[0].font.size = Pt(9)
    current_section = None
    for f in F.FIELDS:
        val = doc["fields"].get(f["key"])
        if val in (None, ""):
            continue
        if f["section"] != current_section:
            current_section = f["section"]
            d.add_heading(current_section, level=2)
        if f["type"] == "textarea":
            d.add_paragraph().add_run(f["label"] + ":").bold = True
            d.add_paragraph(str(val))
        else:
            para = d.add_paragraph()
            para.add_run(f["label"] + ": ").bold = True
            para.add_run(str(val))
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def register_row(doc, approver):
    f = doc["fields"]
    row = {}
    for key, _label in REGISTER_COLUMNS:
        if key == "incident_id":
            row[key] = doc["id"]
        elif key == "approved_at":
            row[key] = storage.now_iso()
        elif key == "intake_method":
            row[key] = doc["intake_method"]
        elif key == "approver":
            row[key] = approver
        else:
            row[key] = f.get(key, "")
    return row


def register_xlsx(rows) -> bytes:
    """Render the full register as an Excel workbook (rebuilt from the
    append-only store, so the durable copy lives in Firestore/SQLite)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Incident Register"
    ws.append([label for _k, label in REGISTER_COLUMNS])
    for c in ws[1]:
        c.font = openpyxl.styles.Font(bold=True)
    for row in rows:
        ws.append([excel_safe(row.get(k, "")) for k, _l in REGISTER_COLUMNS])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_approval_outputs(doc, approver="reviewer", pdf_only=False):
    """Create the approval artifacts, persist them, append the register row.

    pdf_only=True produces just the filled Broadspire PDF — the artifact that
    actually gets filed. The Excel and Word copies are conveniences for editing
    and internal circulation; when they aren't wanted they're noise in the
    storage bucket and on the review screen. The register row is written either
    way: that's the running log, not an export.

    Returns (dict of artifact name -> storage ref, pdf_bytes).
    """
    files = storage.get_files()
    store = storage.get_store()
    out = {}

    if not pdf_only:
        xlsx_bytes = fill_xlsx(doc["fields"])
        out["approved_form.xlsx"] = files.put(doc["id"], "approved_form.xlsx", xlsx_bytes)

    pdf_bytes = fill_pdf(doc["fields"])
    if pdf_bytes:
        out["approved_form.pdf"] = files.put(doc["id"], "approved_form.pdf", pdf_bytes)

    if not pdf_only:
        out["approved_form.docx"] = files.put(doc["id"], "approved_form.docx", fill_docx(doc))

    store.register_append(doc["id"], register_row(doc, approver))
    reg_bytes = register_xlsx(store.register_rows())
    files.put("register", "incident_register.xlsx", reg_bytes)

    doc["files"].update(out)
    return out, pdf_bytes
