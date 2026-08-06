"""Security-review regressions (2026-08-06): B1 + M2-M8 + m9-m16.
Each test maps to a reproduced finding — do not delete without cause.
"""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth as auth_mod
from app import ratelimit
from app.imagegen import MAX_IMAGE_BYTES, _classify, _free
from app.db import init_db
from app.main import app

init_db()  # these tests build clients directly, so create tables up front
RUN = uuid.uuid4().hex[:8]  # unique emails per run, independent of DB state


def _png() -> bytes:
    """A real PNG — the heatmap renderer must be able to open it."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (250, 250, 247)).save(buf, format="PNG")
    return buf.getvalue()


PNG = _png()
BIG_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20_000  # passes MIN_IMAGE_BYTES


def _client(prefix: str, org: str) -> TestClient:
    c = TestClient(app)
    r = c.post(
        "/auth/register",
        json={"email": f"{prefix}-{RUN}@t.dev", "password": "long-enough-1", "org_name": org},
    )
    assert r.status_code == 201, r.text
    return c


def _analysis_with_artifacts(c: TestClient) -> tuple[str, str]:
    """Returns (analysis_id, a real artifact key belonging to it)."""
    project = c.post("/projects", json={"name": "P", "space_type": "hotel"}).json()
    plan = c.post(
        f"/projects/{project['id']}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    aid = c.post(
        f"/projects/{project['id']}/analyses",
        json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"]},
    ).json()["id"]
    for _ in range(60):
        detail = c.get(f"/analyses/{aid}").json()
        if detail["status"] in ("done", "failed", "rejected"):
            break
        time.sleep(0.1)
    flow = next(s for s in detail["steps"] if s["name"] == "flow")
    return aid, flow["output"]["heatmap_key"]


# ---------------------------------------------------------------- B1 (BLOCKER)

def test_artifact_files_are_org_scoped():
    alice = _client("files-a", "OrgFilesA")
    bob = _client("files-b", "OrgFilesB")
    aid, key = _analysis_with_artifacts(alice)

    # Owner can read it through its own analysis
    assert alice.get(f"/analyses/{aid}/files/{key}").status_code == 200

    # Another org holding the leaked key gets nothing — via Alice's analysis id...
    assert bob.get(f"/analyses/{aid}/files/{key}").status_code == 404
    # ...and via its own analysis id (key not owned by it)
    bob_aid, _ = _analysis_with_artifacts(bob)
    assert bob.get(f"/analyses/{bob_aid}/files/{key}").status_code == 404
    # Alice cannot use one of her analyses to read another analysis's key either
    other_aid, _ = _analysis_with_artifacts(alice)
    assert alice.get(f"/analyses/{other_aid}/files/{key}").status_code == 404


def test_file_route_rejects_directory_and_unknown_keys():
    alice = _client("files-c", "OrgFilesC")
    aid, _ = _analysis_with_artifacts(alice)
    assert alice.get(f"/analyses/{aid}/files/.").status_code == 404  # was a 500 (m13)
    assert alice.get(f"/analyses/{aid}/files/nope.png").status_code == 404


# ---------------------------------------------------------------- auth hardening

def test_login_pays_bcrypt_cost_for_unknown_accounts(monkeypatch):
    """M2: no timing oracle — verification runs even when the email is unknown."""
    seen = []
    real = auth_mod.verify_password

    def spy(password, password_hash):
        seen.append(password_hash)
        return real(password, password_hash)

    monkeypatch.setattr(auth_mod, "verify_password", spy)
    with TestClient(app) as c:
        assert c.post("/auth/login", json={"email": f"ghost-{RUN}@t.dev", "password": "whatever-1"}).status_code == 401
    assert seen == [auth_mod.DUMMY_HASH]


def test_long_and_arabic_passwords_register_and_login():
    """M3: >72 bytes must not 500 — an Arabic passphrase is ~2 bytes/char."""
    arabic = "كلمة سر طويلة جدا لفندق كليو في دبي والرياض" * 2
    assert len(arabic.encode()) > 72
    with TestClient(app) as c:
        assert c.post("/auth/register", json={
            "email": f"ar-pass-{RUN}@t.dev", "password": arabic, "org_name": "AR"}).status_code == 201
        c.post("/auth/logout")
        assert c.post("/auth/login", json={"email": f"ar-pass-{RUN}@t.dev", "password": arabic}).status_code == 200
    with TestClient(app) as c:
        assert c.post("/auth/register", json={
            "email": f"long-pass-{RUN}@t.dev", "password": "A" * 200, "org_name": "L"}).status_code == 201


def test_login_inputs_are_bounded():
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"email": "x" * 400 + "@t.dev", "password": "y" * 500})
        assert r.status_code == 422


# ---------------------------------------------------------------- CSRF + CORS

def test_cross_site_state_change_is_blocked():
    """m11: multipart uploads are preflight-free, so SameSite can't be the only guard."""
    alice = _client("csrf", "OrgCsrf")
    r = alice.post("/projects", json={"name": "X"}, headers={"Origin": "http://evil.test"})
    assert r.status_code == 403
    # The real front-end origin still works
    assert alice.post("/projects", json={"name": "X"},
                      headers={"Origin": "http://localhost:3000"}).status_code == 201


def test_rate_limited_response_carries_cors_headers(monkeypatch):
    """M4: a 429 without CORS headers reads as 'server unreachable' in the browser."""
    monkeypatch.setenv("MEYRAKI_RATELIMIT", "on")
    ratelimit.reset()
    try:
        with TestClient(app) as c:
            last = None
            for _ in range(12):
                last = c.post(
                    "/auth/login",
                    json={"email": f"rl2-{RUN}@t.dev", "password": "nope-nope-1"},
                    headers={"Origin": "http://localhost:3000"},
                )
            assert last.status_code == 429
            assert last.headers.get("access-control-allow-origin") == "http://localhost:3000"
            assert last.headers.get("retry-after") == "60"
    finally:
        ratelimit.reset()


# ---------------------------------------------------------------- rate limiter

def _req(method: str = "GET", path: str = "/x", ip: str = "1.2.3.4", headers=None) -> Request:
    return Request({
        "type": "http", "method": method, "path": path, "headers": headers or [],
        "client": (ip, 1234), "scheme": "http", "server": ("t", 80), "query_string": b"",
    })


def test_preflights_do_not_consume_quota(monkeypatch):
    monkeypatch.setenv("MEYRAKI_RATELIMIT", "on")
    ratelimit.reset()
    try:
        for _ in range(50):
            ratelimit.check(_req("OPTIONS", "/auth/login"))  # must never raise
    finally:
        ratelimit.reset()


def test_idle_ip_buckets_are_evicted(monkeypatch):
    """M6: unbounded _hits growth is remote memory exhaustion."""
    monkeypatch.setenv("MEYRAKI_RATELIMIT", "on")
    ratelimit.reset()
    try:
        for i in range(500):
            ratelimit.check(_req(ip=f"10.0.{i // 256}.{i % 256}"))
        assert len(ratelimit._hits) == 500
        ratelimit._last_sweep = 0.0
        ratelimit._sweep(time.monotonic() + ratelimit.WINDOW_SECONDS * 2)
        assert ratelimit._hits == {}
    finally:
        ratelimit.reset()


def test_proxy_ip_used_only_when_trusted(monkeypatch):
    headers = [(b"x-forwarded-for", b"203.0.113.9, 10.0.0.7")]
    monkeypatch.delenv("MEYRAKI_TRUST_PROXY", raising=False)
    assert ratelimit.client_ip(_req(headers=headers, ip="10.0.0.7")) == "10.0.0.7"
    monkeypatch.setenv("MEYRAKI_TRUST_PROXY", "1")
    assert ratelimit.client_ip(_req(headers=headers, ip="10.0.0.7")) == "10.0.0.7"  # last hop


# ---------------------------------------------------------------- imagegen

def test_render_size_cap_rejects_oversized_payloads():
    with pytest.raises(RuntimeError, match="over the size cap"):
        _classify(b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_IMAGE_BYTES + 1), "p")


def test_free_provider_url_cannot_be_path_injected(monkeypatch):
    """M7: prompt text is model/user influenced — it must not steer the URL."""
    captured = {}

    class FakeResponse:
        content = BIG_PNG

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("app.imagegen.httpx.get", fake_get)
    _free("../../../v1/admin#frag?x=1\nsecret")
    assert captured["url"].startswith("https://image.pollinations.ai/prompt/")
    assert "/v1/admin" not in captured["url"]
    assert "\n" not in captured["url"] and "#" not in captured["url"]
