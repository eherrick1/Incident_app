"""Upload intake: normalize every upload into (original file, one PDF) and an
extracted field dict — the same structured record the online form produces.

- .xlsx/.xls  -> deterministic cell read + LibreOffice headless convert to PDF
- images      -> merged into one PDF (Pillow)
- .pdf        -> kept as-is
Then AI vision extraction on the PDF pages (skipped for Excel unless the
deterministic read came back empty).
"""
import io
import subprocess
import tempfile
from pathlib import Path

import config
import extractor


class IntakeError(Exception):
    pass


def xlsx_to_pdf(data: bytes, suffix=".xlsx") -> bytes | None:
    """Convert a workbook to PDF with LibreOffice headless (in the container).
    Returns None if soffice is unavailable (local dev without LibreOffice)."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"form{suffix}"
        src.write_bytes(data)
        try:
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", td, str(src)],
                check=True, capture_output=True, timeout=120)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        pdf = src.with_suffix(".pdf")
        return pdf.read_bytes() if pdf.exists() else None


# A 25 MB upload cap bounds bytes on the wire, not pixels after decompression:
# a crafted PNG a few hundred KB on disk can expand to tens of gigabytes in RAM.
# With one Cloud Run instance that is a full outage, not a slow request. 80 MP
# is far above any phone camera or scanned form this app will legitimately see.
MAX_IMAGE_PIXELS = 80_000_000


def images_to_pdf(images: list[bytes]) -> bytes:
    """Merge photos into a single PDF."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    pages = []
    for data in images:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()                      # force decode inside the guard
        except Image.DecompressionBombError:
            raise IntakeError(
                "One of those images is far larger than a photo or scan should "
                "be, so it wasn't processed. Try re-saving or re-taking it.")
        except Exception:
            raise IntakeError("One of those files could not be read as an image.")
        if img.mode != "RGB":
            img = img.convert("RGB")
        pages.append(img)
    if not pages:
        raise IntakeError("No readable images.")
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


def process_upload(filenames_and_bytes: list[tuple[str, bytes]]):
    """Returns (intake_method, files_dict {name: bytes}, extracted_fields).

    files_dict always includes 'original.<ext>' (first upload) and, when
    available, 'converted.pdf' — the artifact shown on the review screen.
    """
    if not filenames_and_bytes:
        raise IntakeError("No files uploaded.")
    exts = [Path(n).suffix.lower() for n, _ in filenames_and_bytes]
    for ext in exts:
        if ext not in config.ALLOWED_UPLOAD_EXT:
            raise IntakeError(f"File type {ext} is not accepted.")

    files = {}
    name0, data0 = filenames_and_bytes[0]
    ext0 = exts[0]

    if ext0 in (".xlsx", ".xls"):
        method = "excel_upload"
        files[f"original{ext0}"] = data0
        extracted = extractor.extract_from_xlsx(data0)
        pdf = xlsx_to_pdf(data0, ext0)
        if pdf:
            files["converted.pdf"] = pdf
            if not extracted:  # non-standard workbook layout -> AI fallback
                extracted = extractor.extract_from_pdf(pdf)
    elif ext0 == ".pdf":
        method = "pdf_photo_upload"
        files["original.pdf"] = data0
        files["converted.pdf"] = data0
        extracted = extractor.extract_from_pdf(data0)
    else:  # images (one or many) -> merge to one PDF
        method = "pdf_photo_upload"
        image_bytes = [d for (n, d), e in zip(filenames_and_bytes, exts) if e not in (".pdf", ".xlsx", ".xls")]
        for i, ((n, d), e) in enumerate(zip(filenames_and_bytes, exts)):
            files[f"original_{i}{e}"] = d
        pdf = images_to_pdf(image_bytes)
        files["converted.pdf"] = pdf
        extracted = extractor.extract_from_pdf(pdf)

    return method, files, extracted
