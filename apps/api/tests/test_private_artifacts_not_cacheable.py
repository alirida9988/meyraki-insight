"""A CDN cached a client's report and served it to strangers.

Found on the live deployment, not in this suite, and the origin was never wrong: an
anonymous request to the container returned 401 exactly as it should. Cloudflare sat in
front, saw a URL ending in `.pdf`, applied its default static-asset rule, and cached the
response for four hours — after which anyone who knew the analysis id got the document
with no session at all.

    cf-cache-status: HIT   cache-control: max-age=14400   age: 72

Correct authorisation is not sufficient once something else is allowed to remember the
answer. Every route that returns client bytes now says so explicitly, because the edge
cannot know that a 200 was meant for one person unless told.

`private` forbids shared caches, `no-store` forbids writing it down at all, and
`max-age=0` covers intermediaries honouring only the older directive.
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

SECRET = "cache-header-test-secret"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("MEYRAKI_SHARE_SECRET", SECRET)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/auth/register", json={
            "email": f"cache-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "CacheOrg",
        })
        yield c


@pytest.fixture(scope="module")
def analysis(client):
    pid = client.post("/projects", json={"name": "Cache", "space_type": "hotel"}).json()["id"]
    up = client.post(f"/projects/{pid}/uploads", params={"kind": "floorplan"},
                     files={"file": ("p.png", PNG, "image/png")}).json()
    aid = client.post(f"/projects/{pid}/analyses", json={
        "floorplan_upload_id": up["id"], "objectives": ["guest_flow"]}).json()["id"]

    key = storage.save(b"%PDF-1.7\n% a client's priced report\n", ".pdf")
    with SessionLocal() as s:
        step = (s.query(StepRun)
                 .filter(StepRun.analysis_id == aid, StepRun.name == "report").first())
        step.output = {**(step.output or {}), "report_key": key}
        step.status = "done"
        s.get(Analysis, aid).status = "done"
        s.commit()
    return aid, key


def _assert_uncacheable(response, where: str):
    cc = response.headers.get("cache-control", "")
    assert "no-store" in cc, f"{where} may be stored by a cache: {cc!r}"
    assert "private" in cc, f"{where} may be held in a SHARED cache: {cc!r}"


def test_the_owners_report_download_is_not_cacheable(client, analysis):
    aid, _ = analysis
    r = client.get(f"/analyses/{aid}/report")
    assert r.status_code == 200
    _assert_uncacheable(r, "the authenticated report download")


def test_a_shared_client_link_is_not_cacheable(client, analysis):
    """A signed link is time-limited on purpose. A cached copy outlives the expiry and
    turns a deliberately temporary credential into a permanent one."""
    aid, _ = analysis
    url = client.post(f"/analyses/{aid}/share").json()["url"]
    path = "/shared/" + url.split("/shared/")[1]
    r = TestClient(app).get(path)
    assert r.status_code == 200
    _assert_uncacheable(r, "the shared report link")


def test_heatmaps_and_renders_are_not_cacheable(client, analysis):
    """These are .png — the extension a CDN caches most eagerly — and they show the
    client's floor plate."""
    aid, key = analysis
    r = client.get(f"/analyses/{aid}/files/{key}")
    assert r.status_code == 200
    _assert_uncacheable(r, "the artifact file route")


def test_the_route_still_refuses_a_stranger(analysis):
    """The header is a second line, never the first: authorisation must still hold."""
    aid, _ = analysis
    assert TestClient(app).get(f"/analyses/{aid}/report").status_code == 401
