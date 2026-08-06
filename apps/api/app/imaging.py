"""Plan-image preparation (docs/04-REUSE-MAP §1 — pdfplumber MIT, pypdfium2 BSD).

Two real-world problems this solves:

1. A 300 DPI A1 architectural scan is routinely >8000px on its long edge, which
   the vision API rejects outright (observed: 400 "image dimensions exceed max
   allowed size"). Oversized images also cost tokens for no accuracy gain — the
   high-resolution tier tops out at 2576px on the long edge.
2. PDF plans have no raster to draw a heatmap on. pypdfium2 renders one;
   pdfplumber tells a true vector export (extractable wall lines) from a scan
   that someone pasted into a PDF.
"""

import io

from PIL import Image

MAX_EDGE = 2576          # vision high-resolution tier — beyond this is wasted spend
MAX_ENCODED_BYTES = 4_000_000
PDF_DIRECT_LIMIT = 12_000_000  # bigger PDFs get rasterized rather than sent whole
RASTER_DPI = 150

Image.MAX_IMAGE_PIXELS = 300_000_000  # decompression-bomb guard well above real plans


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def normalize_raster(data: bytes) -> bytes:
    """RGB, within MAX_EDGE, within MAX_ENCODED_BYTES. Untouched when already fine."""
    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        needs_resize = max(width, height) > MAX_EDGE
        needs_convert = img.mode not in ("RGB", "L")
        if not needs_resize and not needs_convert and len(data) <= MAX_ENCODED_BYTES:
            return data
        work = img.convert("RGB")
        if needs_resize:
            scale = MAX_EDGE / max(width, height)
            work = work.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                               Image.LANCZOS)
        buf = io.BytesIO()
        work.save(buf, format="PNG")
        if buf.tell() > MAX_ENCODED_BYTES:  # dense scans: PNG can exceed the cap
            buf = io.BytesIO()
            work.save(buf, format="JPEG", quality=88)
        return buf.getvalue()


def rasterize_pdf(data: bytes, dpi: int = RASTER_DPI) -> bytes | None:
    """First page as a normalized PNG, or None if the PDF cannot be rendered."""
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(io.BytesIO(data))
        try:
            if len(pdf) == 0:
                return None
            page = pdf[0]
            image = page.render(scale=dpi / 72).to_pil()
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG")
        finally:
            pdf.close()
        return normalize_raster(buf.getvalue())
    except Exception:
        return None  # caller degrades gracefully — never fails the analysis


def pdf_vector_stats(data: bytes) -> dict:
    """Line/rect/char counts on page 1 — deterministic vector-vs-scan evidence."""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if not pdf.pages:
                return {"readable": False}
            page = pdf.pages[0]
            lines, rects = len(page.lines), len(page.rects)
            curves, chars = len(page.curves), len(page.chars)
            return {
                "readable": True,
                "lines": lines,
                "rects": rects,
                "curves": curves,
                "chars": chars,
                # Structural evidence: a CAD export draws its walls as path
                # objects; a scan pasted into a PDF has none (an OCR text layer
                # alone must not count, hence strokes only).
                "is_vector": (lines + rects + curves) >= 3,
            }
    except Exception:
        return {"readable": False}


def plan_raster(data: bytes) -> bytes | None:
    """A raster of any plan (PDF included) suitable for drawing the heatmap on."""
    if is_pdf(data):
        return rasterize_pdf(data)
    try:
        return normalize_raster(data)
    except Exception:
        return None


def vision_payload(data: bytes) -> tuple[str, bytes]:
    """('document'|'image', bytes) — what to send a vision model for this plan."""
    if is_pdf(data):
        if len(data) <= PDF_DIRECT_LIMIT:
            return "document", data
        raster = rasterize_pdf(data)  # too large to send whole; a page render still reads
        if raster is not None:
            return "image", raster
        return "document", data
    return "image", normalize_raster(data)
