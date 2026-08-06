"""Regression tests for the adversarial-review findings (2026-08-06).
Each test corresponds to a confirmed, reproduced bug — do not delete without cause.
"""

import os
import time
from datetime import datetime, timedelta, timezone

os.environ["DATABASE_URL"] = "sqlite:///./test_meyraki.db"
os.environ["UPLOAD_DIR"] = "var/test_uploads"
os.environ["MEYRAKI_USE_AGENTS"] = "off"

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Analysis
from app.pipeline import STALE_AFTER

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
CSV = b"zone_name,timestamp,traffic_count\nLobby,2025-04-20 08:00,120\n"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def project_id(client):
    return client.post("/projects", json={"name": "Hardening", "space_type": "cafe"}).json()["id"]


def _analysis(client, project_id) -> str:
    plan = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    r = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"]},
    )
    return r.json()["id"]


def _wait_done(client, analysis_id, tries=50):
    for _ in range(tries):
        d = client.get(f"/analyses/{analysis_id}").json()
        if d["status"] in ("done", "failed", "rejected"):
            return d
        time.sleep(0.1)
    return d


# Finding #5: spoofed content type must be rejected by magic-byte sniffing
def test_spoofed_floorplan_content_rejected(client, project_id):
    r = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("fake.png", b"just ascii text, not an image", "image/png")},
    )
    assert r.status_code == 422
    assert "not a valid PNG" in r.json()["detail"]


# Finding #4: 400-char filename must not 500 or orphan a file
def test_long_filename_truncated_not_500(client, project_id):
    r = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("a" * 400 + ".png", PNG, "image/png")},
    )
    assert r.status_code == 201
    assert len(r.json()["filename"]) <= 200


# Finding #3: SSE for unknown analysis must 404 immediately, not hang
def test_sse_unknown_analysis_404(client):
    r = client.get("/analyses/doesnotexist123/events")
    assert r.status_code == 404


# Finding #1: a crashed run (stale heartbeat) must be resumable
def test_crashed_running_analysis_is_resumable(client, project_id):
    aid = _analysis(client, project_id)
    _wait_done(client, aid)

    with SessionLocal() as s:
        a = s.get(Analysis, aid)
        a.status = "running"  # simulate a worker that died mid-run
        a.heartbeat_at = datetime.now(timezone.utc) - STALE_AFTER - timedelta(minutes=1)
        for step in a.steps:
            step.status = "pending"
            step.output = None
        s.commit()

    r = client.post(f"/analyses/{aid}/resume")
    assert r.status_code == 200, r.json()
    d = _wait_done(client, aid)
    assert d["status"] == "done"


# Finding #1 counterpart: a LIVE run (fresh heartbeat) must NOT be resumable
def test_live_running_analysis_not_resumable(client, project_id):
    aid = _analysis(client, project_id)
    _wait_done(client, aid)
    with SessionLocal() as s:
        a = s.get(Analysis, aid)
        a.status = "running"
        a.heartbeat_at = datetime.now(timezone.utc)
        s.commit()
    r = client.post(f"/analyses/{aid}/resume")
    assert r.status_code == 409
    with SessionLocal() as s:  # restore
        a = s.get(Analysis, aid)
        a.status = "done"
        s.commit()


# Finding #2: the runner's atomic claim — a second runner must no-op, not re-execute
def test_runner_claim_prevents_double_execution(client, project_id):
    from app.models import Event
    from app.pipeline import run_analysis

    aid = _analysis(client, project_id)
    _wait_done(client, aid)
    with SessionLocal() as s:
        a = s.get(Analysis, aid)
        a.status = "running"
        a.heartbeat_at = datetime.now(timezone.utc)  # live run owned by someone else
        s.commit()
        before = s.query(Event).filter_by(analysis_id=aid).count()
    with SessionLocal() as s:
        run_analysis(s, aid)  # must lose the claim and return immediately
    with SessionLocal() as s:
        after = s.query(Event).filter_by(analysis_id=aid).count()
        a = s.get(Analysis, aid)
        a.status = "done"
        s.commit()
    assert after == before


# Finding #6: report step must output a ReportArtifact, not a QAVerdict
def test_report_step_outputs_report_artifact(client, project_id):
    aid = _analysis(client, project_id)
    d = _wait_done(client, aid)
    report = next(s for s in d["steps"] if s["name"] == "report")
    assert "report_key" in report["output"]
    assert "passed" not in report["output"]


# Review finding #1 (2026-08-06): crash between "intake done" and the rejection
# commit must still reject on resume — never run the paid pipeline on a non-floorplan.
def test_resume_after_crash_still_rejects_non_floorplan(client, project_id):
    aid = _analysis(client, project_id)
    _wait_done(client, aid)

    with SessionLocal() as s:
        a = s.get(Analysis, aid)
        a.status = "running"
        a.heartbeat_at = datetime.now(timezone.utc) - STALE_AFTER - timedelta(minutes=1)
        a.error = None
        for step in a.steps:
            if step.name == "intake":
                step.status = "done"
                step.output = {
                    "plan_quality": "not_a_floorplan",
                    "plan_kind": "raster",
                    "has_scale_hint": False,
                    "floors_detected": 1,
                    "space_type_detected": "other",
                    "footfall": "none",
                    "footfall_errors": [],
                    "warnings": [],
                    "rejection_reason": "This looks like a photo, not a floorplan.",
                }
            else:
                step.status = "pending"
                step.output = None
        s.commit()

    assert client.post(f"/analyses/{aid}/resume").status_code == 200
    d = _wait_done(client, aid)
    assert d["status"] == "rejected"
    assert d["error"] == "This looks like a photo, not a floorplan."
    steps = {s["name"]: s["status"] for s in d["steps"]}
    assert steps["zones"] == "pending"  # nothing past intake ever ran


# Review F3 (2026-08-06): missing report file must 404, never 500
def test_download_report_missing_file_404(client, project_id):
    aid = _analysis(client, project_id)
    _wait_done(client, aid)
    with SessionLocal() as s:
        a = s.get(Analysis, aid)
        for step in a.steps:
            if step.name == "report":
                step.output = {"report_key": "deadbeef00.pdf", "language": "en", "sections": []}
        s.commit()
    r = client.get(f"/analyses/{aid}/report.pdf")
    assert r.status_code == 404
    assert "no longer available" in r.json()["detail"]
