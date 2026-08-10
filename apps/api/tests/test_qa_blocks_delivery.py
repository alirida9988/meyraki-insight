"""The QA Verifier labelled a bad analysis but did not stop it reaching a client.

`docs/05-BUILD-PLAN.md` lists as a quality gate: "QA Verifier step blocks delivery of
internally inconsistent results." It did not. The verifier runs as step 9, *after* the
report is written in step 8, so the PDF already exists on disk by the time the
inconsistency is found. A failed verdict set `analysis.status = "failed"` and stopped
there — neither the owner download nor, more seriously, the signed client link checked
that status.

So a studio could mint an expiring link for an analysis whose layout proposals reference
zones that do not exist in the zone graph, and the client would open a perfectly rendered
report describing moves on rooms the model never found.

The fix draws the line at delivery rather than at access: the owner may still download a
failed analysis's report, because seeing what went wrong is diagnosis and it is their own
data, but the client-facing link refuses to mint and refuses to serve.

Redemption is checked as well as minting, because status can change after a link is sent:
an analysis that was clean when shared can be re-run and fail, and a link already in a
client's inbox must go dead rather than keep serving the superseded document.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest
from fastapi.testclient import TestClient

from app import sharing, storage
from app.db import SessionLocal
from app.main import app
from app.models import Analysis, StepRun
from conftest import VALID_PNG

SECRET = "test-qa-delivery-secret"
PNG = VALID_PNG


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("MEYRAKI_SHARE_SECRET", SECRET)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/auth/register", json={
            "email": f"qa-delivery-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "QADeliveryOrg",
        })
        yield c


def _analysis_with_report(client, status: str) -> str:
    """An analysis carrying a real report artifact, forced into `status`."""
    project_id = client.post(
        "/projects", json={"name": "QA delivery", "space_type": "hotel"}
    ).json()["id"]
    upload = client.post(
        f"/projects/{project_id}/uploads", params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    aid = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": upload["id"], "objectives": ["guest_flow"]},
    ).json()["id"]

    key = storage.save(b"%PDF-1.7\n% report from an inconsistent analysis\n", ".pdf")
    with SessionLocal() as session:
        step = (
            session.query(StepRun)
            .filter(StepRun.analysis_id == aid, StepRun.name == "report")
            .first()
        )
        step.output = {**(step.output or {}), "report_key": key}
        step.status = "done"
        analysis = session.get(Analysis, aid)
        analysis.status = status
        analysis.error = "References unknown zones: ['ghost_zone']"
        session.commit()
    return aid


def test_a_failed_analysis_cannot_be_shared_with_a_client(client):
    """The repro: the layout referenced a zone the Zone Analyst never produced."""
    aid = _analysis_with_report(client, "failed")
    r = client.post(f"/analyses/{aid}/share")
    assert r.status_code == 409, f"minted a client link for a failed analysis ({r.status_code})"
    assert "did not pass" in r.json()["detail"].lower() or "qa" in r.json()["detail"].lower()


def test_a_link_minted_before_a_rerun_stops_working_when_the_rerun_fails(client):
    """Status is re-checked at redemption, not only when the link was created."""
    aid = _analysis_with_report(client, "done")
    url = client.post(f"/analyses/{aid}/share").json()["url"]
    path = "/shared/" + url.split("/shared/")[1]

    anonymous = TestClient(app)
    assert anonymous.get(path).status_code == 200  # valid while the analysis is clean

    with SessionLocal() as session:  # the analysis is re-run and this time it fails QA
        session.get(Analysis, aid).status = "failed"
        session.commit()

    assert anonymous.get(path).status_code == 404, "a superseded report kept serving"


def test_a_clean_analysis_is_still_shareable(client):
    """The guard must not break the feature it protects."""
    aid = _analysis_with_report(client, "done")
    assert client.post(f"/analyses/{aid}/share").status_code == 200


def test_the_owner_may_still_download_their_own_failed_report(client):
    """Deliberately allowed: the studio owns the data and needs to see what went wrong.
    The line is drawn at delivery to a client, not at access by the author."""
    aid = _analysis_with_report(client, "failed")
    r = client.get(f"/analyses/{aid}/report")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
