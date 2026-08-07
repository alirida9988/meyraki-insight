"""Live QA hunt — realistic things a real user does that tests never tried.

Run against a live server (uvicorn on :8000) with real agents ON.
Not part of the offline suite: it spends model credits and needs the network.
    .venv/bin/python tests/qa_hunt.py
"""

import io
import sys
import time

import httpx
from PIL import Image

BASE = "http://localhost:8000"
RUN = str(int(time.time()))
# Poll deadline per analysis. Raised from 120 when the moodboard step started pacing its
# renders: the free provider 429s on back-to-back requests, so three renders are spaced
# ~12s apart to deliver 3/3 instead of 1/3, costing ~60s. A deadline below the real
# pipeline time reports a product bug that is really our own impatience.
POLL_TICKS = 240   # x 2s = 480s
results: list[tuple[str, str, str]] = []  # (verdict, scenario, detail)


def record(ok: bool | None, scenario: str, detail: str = "") -> None:
    verdict = "PASS" if ok else ("BUG " if ok is False else "INFO")
    results.append((verdict, scenario, detail))
    print(f"[{verdict}] {scenario}" + (f" — {detail}" if detail else ""), flush=True)


def real_plan(long_edge: int | None = None, mode: str = "RGB") -> bytes:
    """The real Cleo floorplan, optionally upscaled — a genuine 300 DPI scan case.
    Synthetic box diagrams are (correctly) rejected by the Intake Agent, so the
    oversized-plan path must be proven with a plan a human would recognise."""
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "..", "web", "e2e", "fixtures", "cleo_plan.png")
    img = Image.open(path).convert(mode)
    if long_edge:
        scale = long_edge / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png(w: int, h: int, mode: str = "RGB") -> bytes:
    """A plan-ish raster: walls + labels so vision has something real to read."""
    img = Image.new(mode, (w, h), 255 if mode in ("L", "1") else (250, 250, 247))
    d = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(img)
    m = min(w, h) // 20
    d.rectangle([m, m, w - m, h - m], outline=0 if mode in ("L", "1") else (22, 19, 14), width=max(2, m // 10))
    d.line([w // 2, m, w // 2, h - m], fill=0 if mode in ("L", "1") else (22, 19, 14), width=max(2, m // 12))
    d.line([m, h // 2, w // 2, h // 2], fill=0 if mode in ("L", "1") else (22, 19, 14), width=max(2, m // 12))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def real_photo() -> bytes:
    """The real plan re-encoded as a JPEG — a phone photo of a printed plan."""
    img = Image.open(io.BytesIO(real_plan())).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def jpeg(w: int, h: int) -> bytes:
    img = Image.open(io.BytesIO(png(w, h))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def pdf_plan() -> bytes:
    """A vector PDF floorplan the way an architect exports one."""
    from reportlab.lib.pagesizes import A3
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A3)
    w, h = A3
    c.setLineWidth(3)
    c.rect(40, 40, w - 80, h - 80)
    c.line(w / 2, 40, w / 2, h - 40)
    c.line(40, h / 2, w / 2, h / 2)
    c.line(w / 2, h * 0.7, w - 40, h * 0.7)      # cafe / back-of-house split
    c.line(w * 0.75, h * 0.7, w * 0.75, h - 40)  # service corridor
    c.rect(w * 0.1, h * 0.1, 90, 60)             # reception desk
    c.rect(w * 0.55, h * 0.2, 70, 70)            # bar counter
    c.rect(w * 0.6, h * 0.75, 60, 40)            # restroom block
    c.setFont("Helvetica", 14)
    c.drawString(90, h / 2 + 40, "LOBBY")
    c.drawString(w / 2 + 60, h / 2, "CAFE")
    c.drawString(90, 90, "ENTRANCE")
    c.showPage()
    c.save()
    return buf.getvalue()


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=300, follow_redirects=True)
    r = c.post("/auth/register", json={
        "email": f"qa-{RUN}@test.dev", "password": "qa-password-1", "org_name": f"QA {RUN}"})
    if r.status_code != 201:
        print("cannot register:", r.status_code, r.text)
        return 2
    project = c.post("/projects", json={"name": f"QA {RUN}", "space_type": "cafe"}).json()
    pid = project["id"]

    def upload(name: str, data: bytes, kind: str = "floorplan", ctype: str = "image/png"):
        return c.post(f"/projects/{pid}/uploads", params={"kind": kind},
                      files={"file": (name, data, ctype)})

    # 1. Oversized scan — a 300 DPI A1 architectural scan is routinely >8000px
    big = real_plan(long_edge=9000)
    r = upload("a1_scan.png", big)
    record(r.status_code == 201, "huge 9000x4200 plan accepted by upload",
           f"{r.status_code}, {len(big)//1024}KB")
    big_id = r.json().get("id") if r.status_code == 201 else None

    # 2. JPEG plan (phone photo of a printed plan — extremely common)
    r = upload("plan_photo.jpg", real_photo(), ctype="image/jpeg")
    record(r.status_code == 201, "JPEG plan accepted", str(r.status_code))
    jpg_id = r.json().get("id") if r.status_code == 201 else None

    # 3. Grayscale PNG (scanner default)
    r = upload("gray.png", png(1600, 1200, mode="L"))
    record(r.status_code == 201, "grayscale PNG accepted", str(r.status_code))

    # 4. Vector PDF from an architect
    try:
        pdf_bytes = pdf_plan()
        r = upload("plan.pdf", pdf_bytes, ctype="application/pdf")
        record(r.status_code == 201, "vector PDF accepted", str(r.status_code))
        pdf_id = r.json().get("id") if r.status_code == 201 else None
    except ImportError:
        record(None, "vector PDF", "reportlab not installed — skipped")
        pdf_id = None

    # 5. European Excel CSV (semicolon delimiter) — silently rejected today?
    euro = "zone_name;timestamp;traffic_count\nLobby;2025-04-20 08:00;120\n".encode()
    r = upload("euro.csv", euro, kind="footfall", ctype="text/csv")
    record(r.status_code == 422, "semicolon CSV rejected with guidance",
           str(r.json().get("detail", ""))[:120] if r.status_code == 422 else str(r.status_code))

    # 6. UTF-16 CSV (Excel "Unicode Text" export)
    utf16 = "zone_name,timestamp,traffic_count\nLobby,2025-04-20 08:00,120\n".encode("utf-16")
    r = upload("utf16.csv", utf16, kind="footfall", ctype="text/csv")
    record(r.status_code == 422, "UTF-16 CSV rejected with guidance",
           str(r.json().get("detail", ""))[:120] if r.status_code == 422 else str(r.status_code))

    # 7. Valid CSV with an empty trailing line + CRLF (Windows Excel)
    win = b"zone_name,timestamp,traffic_count\r\nLobby,2025-04-20 08:00,120\r\n\r\n"
    r = upload("win.csv", win, kind="footfall", ctype="text/csv")
    record(r.status_code == 201, "Windows CRLF CSV with trailing blank line accepted",
           str(r.status_code) + " " + str(r.json())[:80])

    # 8. Analysis history: can a user who refreshes find their analyses again?
    r = c.get(f"/projects/{pid}/analyses")
    record(r.status_code == 200, "GET /projects/{id}/analyses (history after refresh)",
           f"{r.status_code}" + (" — no endpoint, refresh loses the analysis" if r.status_code == 405 or r.status_code == 404 else ""))

    # 9. The real test: run the oversized plan through the pipeline
    if big_id:
        r = c.post(f"/projects/{pid}/analyses",
                   json={"floorplan_upload_id": big_id, "objectives": ["guest_flow"]})
        aid = r.json().get("id")
        detail = {}
        for _ in range(POLL_TICKS):
            detail = c.get(f"/analyses/{aid}").json()
            if detail["status"] in ("done", "failed", "rejected"):
                break
            time.sleep(2)
        failed_step = next((s for s in detail.get("steps", []) if s["status"] == "failed"), None)
        record(detail.get("status") == "done", "oversized plan completes the pipeline",
               f"{detail.get('status')}" + (f" @ {failed_step['name']}: {(failed_step.get('error') or '')[:150]}" if failed_step else ""))

    # 10. PDF plan through the pipeline (heatmap expected?)
    if pdf_id:
        r = c.post(f"/projects/{pid}/analyses",
                   json={"floorplan_upload_id": pdf_id, "objectives": ["guest_flow"]})
        aid = r.json().get("id")
        detail = {}
        for _ in range(POLL_TICKS):
            detail = c.get(f"/analyses/{aid}").json()
            if detail["status"] in ("done", "failed", "rejected"):
                break
            time.sleep(2)
        steps = {s["name"]: s for s in detail.get("steps", [])}
        heat = (steps.get("flow", {}).get("output") or {}).get("heatmap_key")
        record(detail.get("status") == "done", "PDF plan completes the pipeline", str(detail.get("status")))
        record(bool(heat), "PDF plan produces a heatmap",
               "no heatmap — PDF users get a report without the flow visual" if not heat else "ok")
        report = (steps.get("report", {}).get("output") or {}).get("report_key")
        record(bool(report), "PDF plan produces a report", str(bool(report)))

    # 11. Two analyses at once on one project (impatient user clicks twice on two tabs)
    if jpg_id:
        a1 = c.post(f"/projects/{pid}/analyses",
                    json={"floorplan_upload_id": jpg_id, "objectives": ["guest_flow"]}).json().get("id")
        a2 = c.post(f"/projects/{pid}/analyses",
                    json={"floorplan_upload_id": jpg_id, "objectives": ["ambiance"]}).json().get("id")
        record(a1 != a2 and bool(a1) and bool(a2), "two concurrent analyses accepted", f"{a1} / {a2}")
        for aid in (a1, a2):
            for _ in range(POLL_TICKS):
                d = c.get(f"/analyses/{aid}").json()
                if d["status"] in ("done", "failed", "rejected"):
                    break
                time.sleep(2)
            record(d["status"] == "done", f"concurrent analysis {aid[:6]} finished", d["status"] +
                   (f" — {(d.get('error') or '')[:120]}" if d["status"] != "done" else ""))

    # 12. Scenario feasibility promise: is solver_feasible ever true?
    d = c.get(f"/analyses/{a1}").json() if jpg_id else {}
    layout = next((s["output"] for s in d.get("steps", []) if s["name"] == "layout"), None)
    if layout:
        flags = [s.get("solver_feasible") for s in layout["scenarios"]]
        record(any(flags), "layout scenarios carry a real feasibility verdict",
               f"solver_feasible={flags} — the documented 'solver guarantees' step is not implemented"
               if not any(flags) else str(flags))

    # 16. Cost receipt + resume. A resumed analysis used to be handed a fresh budget and
    # to lose its receipt entirely, so both are checked against the live stack.
    if jpg_id:
        detail = c.get(f"/analyses/{a1}").json()
        cost = detail.get("cost") or {}
        record(isinstance(cost.get("usd"), (int, float)) and cost.get("by_step") is not None,
               "analysis reports what it cost",
               f"${cost.get('usd')} over {len(cost.get('by_step') or [])} calls")

        before, rows_before = cost.get("usd") or 0.0, len(cost.get("by_step") or [])
        r = c.post(f"/analyses/{a1}/resume")
        if r.status_code in (200, 202):
            for _ in range(POLL_TICKS):
                after_detail = c.get(f"/analyses/{a1}").json()
                if after_detail["status"] in ("done", "failed", "rejected"):
                    break
                time.sleep(2)
            after_cost = after_detail.get("cost") or {}
            after, rows_after = after_cost.get("usd") or 0.0, len(after_cost.get("by_step") or [])
            record(after >= before and rows_after >= rows_before,
                   "resume keeps the earlier receipt instead of losing it",
                   f"${before:.4f}/{rows_before} rows -> ${after:.4f}/{rows_after} rows")
        else:
            record(r.status_code == 409,
                   "resume declined on a finished analysis", str(r.status_code))

    print("\n" + "=" * 70)
    bugs = [r for r in results if r[0] == "BUG "]
    print(f"{len(results)} checks · {len(bugs)} BUGS")
    for _, scenario, detail in bugs:
        print(f"  BUG: {scenario} — {detail}")
    return 1 if bugs else 0


if __name__ == "__main__":
    sys.exit(main())
