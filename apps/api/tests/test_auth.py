"""Auth, org isolation, and rate limiting (M5 security pass)."""

import os

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def _register(c: TestClient, email: str, org: str) -> None:
    r = c.post("/auth/register", json={"email": email, "password": "long-enough-1", "org_name": org})
    assert r.status_code == 201, r.text


@pytest.fixture()
def alice():
    with TestClient(app) as c:
        _register(c, "alice@a.dev", "OrgA")
        yield c


@pytest.fixture()
def bob():
    with TestClient(app) as c:
        _register(c, "bob@b.dev", "OrgB")
        yield c


def test_register_login_me_logout():
    with TestClient(app) as c:
        _register(c, "flow@t.dev", "FlowOrg")
        assert c.get("/auth/me").json()["email"] == "flow@t.dev"
        assert c.post("/auth/logout").json() == {"ok": True}
        assert c.get("/auth/me").status_code == 401
        assert c.post("/auth/login", json={"email": "flow@t.dev", "password": "wrong-password"}).status_code == 401
        assert c.post("/auth/login", json={"email": "flow@t.dev", "password": "long-enough-1"}).status_code == 200
        assert c.get("/auth/me").json()["org"] == "FlowOrg"


def test_register_validation():
    with TestClient(app) as c:
        assert c.post("/auth/register", json={"email": "not-an-email", "password": "long-enough-1", "org_name": "X"}).status_code == 422
        assert c.post("/auth/register", json={"email": "a@b.co", "password": "short", "org_name": "X"}).status_code == 422
        _register(c, "dup@t.dev", "X")
        assert c.post("/auth/register", json={"email": "dup@t.dev", "password": "long-enough-1", "org_name": "Y"}).status_code == 409


def test_unauthenticated_requests_rejected():
    with TestClient(app) as c:
        assert c.get("/projects").status_code == 401
        assert c.post("/projects", json={"name": "X"}).status_code == 401
        assert c.get("/analyses/whatever").status_code == 401
        assert c.get("/analyses/x/files/whatever.png").status_code == 401
        assert c.get("/analyses/x/events").status_code == 401
        assert c.get("/analyses/x/report").status_code == 401


def test_org_isolation_end_to_end(alice, bob):
    project = alice.post("/projects", json={"name": "Secret Hotel", "space_type": "hotel"}).json()
    plan = alice.post(
        f"/projects/{project['id']}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    analysis = alice.post(
        f"/projects/{project['id']}/analyses",
        json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"]},
    ).json()

    # Bob sees nothing of Alice's org — uniformly 404, never 403 (no existence leak)
    assert all(p["id"] != project["id"] for p in bob.get("/projects").json())
    assert bob.post(f"/projects/{project['id']}/uploads", params={"kind": "floorplan"},
                    files={"file": ("p.png", PNG, "image/png")}).status_code == 404
    assert bob.post(f"/projects/{project['id']}/analyses",
                    json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"]}).status_code == 404
    assert bob.get(f"/analyses/{analysis['id']}").status_code == 404
    assert bob.post(f"/analyses/{analysis['id']}/resume").status_code == 404
    assert bob.get(f"/analyses/{analysis['id']}/events").status_code == 404
    assert bob.get(f"/analyses/{analysis['id']}/report").status_code == 404
    # Alice still sees her own
    assert alice.get(f"/analyses/{analysis['id']}").status_code == 200


def test_rate_limit_on_login(monkeypatch):
    monkeypatch.setenv("MEYRAKI_RATELIMIT", "on")
    ratelimit.reset()
    try:
        with TestClient(app) as c:
            statuses = [
                c.post("/auth/login", json={"email": "rl@t.dev", "password": "nope-nope-1"}).status_code
                for _ in range(12)
            ]
        assert statuses[:10] == [401] * 10
        assert statuses[10] == 429 and statuses[11] == 429
    finally:
        ratelimit.reset()
