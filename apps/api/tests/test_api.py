import os
import time

os.environ["DATABASE_URL"] = "sqlite:///./test_meyraki.db"
os.environ["UPLOAD_DIR"] = "var/test_uploads"
os.environ["MEYRAKI_USE_AGENTS"] = "off"

import pytest
from fastapi.testclient import TestClient

from app.main import app

GOOD_CSV = (
    b"zone_name,timestamp,traffic_count\n"
    b"Lobby,2025-04-20 08:00,120\n"
    b"Reception,2025-04-20 08:00,80\n"
)
BAD_CSV = b"zone,when,count\nLobby,yesterday,many\n"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(scope="module")
def client():
    if os.path.exists("test_meyraki.db"):
        os.remove("test_meyraki.db")
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def project_id(client):
    r = client.post("/projects", json={"name": "Cleo Urban Stay", "space_type": "hotel"})
    assert r.status_code == 201
    return r.json()["id"]


def test_footfall_upload_rejects_bad_schema(client, project_id):
    r = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "footfall"},
        files={"file": ("data.csv", BAD_CSV, "text/csv")},
    )
    assert r.status_code == 422
    assert any("Missing required column" in e for e in r.json()["detail"]["errors"])


def test_floorplan_rejects_wrong_type(client, project_id):
    r = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("plan.dwg", b"x" * 10, "application/acad")},
    )
    assert r.status_code == 422


def test_full_pipeline_runs_to_done(client, project_id):
    plan = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("plan.png", PNG_MAGIC, "image/png")},
    ).json()
    ff = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "footfall"},
        files={"file": ("data.csv", GOOD_CSV, "text/csv")},
    ).json()
    assert ff["meta"]["rows"] == 2

    r = client.post(
        f"/projects/{project_id}/analyses",
        json={
            "floorplan_upload_id": plan["id"],
            "footfall_upload_id": ff["id"],
            "objectives": ["guest_flow", "revenue_per_sqm"],
        },
    )
    assert r.status_code == 201
    analysis_id = r.json()["id"]

    for _ in range(50):  # background task; TestClient runs it after response
        detail = client.get(f"/analyses/{analysis_id}").json()
        if detail["status"] in ("done", "failed", "rejected"):
            break
        time.sleep(0.1)

    assert detail["status"] == "done", detail.get("error")
    steps = {s["name"]: s for s in detail["steps"]}
    assert all(s["status"] == "done" for s in steps.values())
    assert steps["routing"]["output"]["track"] == "data_driven"
    assert steps["qa"]["output"]["passed"] is True


def test_analysis_validates_upload_ownership(client, project_id):
    r = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": "nonexistent", "objectives": ["guest_flow"]},
    )
    assert r.status_code == 422


def test_analysis_accepts_report_language(client, project_id):
    plan = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("p.png", PNG_MAGIC, "image/png")},
    ).json()
    r = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"],
              "report_language": "ar"},
    )
    assert r.status_code == 201
    bad = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"],
              "report_language": "fr"},
    )
    assert bad.status_code == 422
