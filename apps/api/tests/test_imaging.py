"""Plan-image preparation: oversized scans, PDFs, scans-in-PDFs (docs/04 §1)."""

import io

from PIL import Image

from app import imaging


def _plan_png(w: int, h: int, mode: str = "RGB") -> bytes:
    img = Image.new(mode, (w, h), 255 if mode == "L" else (250, 250, 247))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _vector_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 400))
    c.rect(20, 20, 560, 360)
    c.line(300, 20, 300, 380)
    c.line(20, 200, 300, 200)
    c.drawString(60, 220, "LOBBY")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_oversized_scan_is_brought_within_the_vision_limit():
    """A 300 DPI A1 scan is >8000px and the API rejects it outright."""
    out = imaging.normalize_raster(_plan_png(9000, 4200))
    assert max(Image.open(io.BytesIO(out)).size) <= imaging.MAX_EDGE
    assert len(out) <= imaging.MAX_ENCODED_BYTES


def test_reasonable_plan_is_not_re_encoded():
    data = _plan_png(1200, 900)
    assert imaging.normalize_raster(data) is data or imaging.normalize_raster(data) == data


def test_grayscale_and_palette_modes_survive():
    assert imaging.normalize_raster(_plan_png(800, 600, mode="L"))
    palette = Image.new("P", (800, 600))
    buf = io.BytesIO()
    palette.save(buf, format="PNG")
    assert Image.open(io.BytesIO(imaging.normalize_raster(buf.getvalue()))).mode == "RGB"


def test_pdf_rasterizes_for_the_heatmap():
    raster = imaging.rasterize_pdf(_vector_pdf())
    assert raster and raster[:8] == b"\x89PNG\r\n\x1a\n"
    assert max(Image.open(io.BytesIO(raster)).size) <= imaging.MAX_EDGE


def test_vector_pdf_distinguished_from_a_scan_in_a_pdf():
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    assert imaging.pdf_vector_stats(_vector_pdf())["is_vector"] is True

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(600, 400))
    c.drawImage(ImageReader(io.BytesIO(_plan_png(1200, 800))), 0, 0, 600, 400)
    c.showPage()
    c.save()
    assert imaging.pdf_vector_stats(buf.getvalue())["is_vector"] is False


def test_garbage_never_raises():
    assert imaging.rasterize_pdf(b"not a pdf") is None
    assert imaging.plan_raster(b"garbage") is None
    assert imaging.pdf_vector_stats(b"garbage") == {"readable": False}


def test_vision_payload_routing():
    kind, payload = imaging.vision_payload(_vector_pdf())
    assert kind == "document"
    kind, payload = imaging.vision_payload(_plan_png(9000, 4200))
    assert kind == "image" and max(Image.open(io.BytesIO(payload)).size) <= imaging.MAX_EDGE


def test_heatmap_now_renders_over_a_pdf_plan():
    from app.flow import simulated
    from app.heatmap import render
    from app.agents import WireZoneGraph, repair_zone_graph
    from tests.test_agents import _wz

    graph = repair_zone_graph(WireZoneGraph(zones=[_wz("lobby")], adjacency=[], entrances=["lobby"]))
    png = render(_vector_pdf(), graph, simulated(graph))
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
