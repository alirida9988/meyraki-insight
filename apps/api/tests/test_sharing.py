"""Signed report links — the M5 security-pass item that had not been built.

A link that carries its own authority is a credential written down in a URL, so every
test here is about what it must NOT do: outlive its deadline, survive tampering, work on
a different analysis, or tell an attacker which analyses exist.
"""

import os
import time

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest
from fastapi.testclient import TestClient

from app import sharing
from app.main import app

SECRET = "test-share-secret-long-and-random"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("MEYRAKI_SHARE_SECRET", SECRET)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/auth/register", json={
            "email": f"share-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "ShareOrg",
        })
        yield c


@pytest.fixture(scope="module")
def analysis(client) -> str:
    """A finished analysis with a real PDF on disk.

    With agents off the report step is a stub and produces no document — which the share
    endpoint correctly refuses to mint a link for. These tests are about the link, so the
    artifact is stored directly rather than paying a model to write one.
    """
    from app import storage
    from app.db import SessionLocal
    from app.models import Analysis, StepRun

    project_id = client.post(
        "/projects", json={"name": "Sharing", "space_type": "cafe"}
    ).json()["id"]
    upload = client.post(
        f"/projects/{project_id}/uploads", params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    aid = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": upload["id"], "objectives": ["guest_flow"]},
    ).json()["id"]
    for _ in range(60):
        if client.get(f"/analyses/{aid}").json()["status"] in ("done", "failed", "rejected"):
            break
        time.sleep(0.1)

    key = storage.save(b"%PDF-1.7\n% a stand-in for the real deliverable\n", ".pdf")
    with SessionLocal() as session:
        step = (
            session.query(StepRun)
            .filter(StepRun.analysis_id == aid, StepRun.name == "report")
            .first()
        )
        step.output = {**(step.output or {}), "report_key": key,
                       "language": "en", "sections": {"executive_summary": "s"}}
        step.status = "done"
        session.commit()
    return aid


# ---------------------------------------------------------------- the token itself

def test_a_token_is_only_valid_for_its_own_analysis():
    """A leaked link must expose exactly the document it was minted for."""
    signature, expires = sharing.mint("analysis-a")
    assert sharing.verify("analysis-a", expires, signature)
    assert not sharing.verify("analysis-b", expires, signature)


def test_pushing_the_deadline_out_invalidates_the_token():
    """The expiry is inside the signature, so it cannot be edited in the URL."""
    signature, expires = sharing.mint("a1", ttl_seconds=3600)
    assert sharing.verify("a1", expires, signature)
    assert not sharing.verify("a1", expires + 86_400, signature)


def test_an_expired_token_stops_working():
    signature, expires = sharing.mint("a1", ttl_seconds=60)
    assert sharing.verify("a1", expires, signature)
    # the signature is still authentic; only the deadline has passed
    past = int(time.time()) - 10
    expired_sig, _ = sharing._signature("a1", past), None
    assert not sharing.verify("a1", past, expired_sig)


def test_a_forged_or_truncated_signature_is_rejected():
    signature, expires = sharing.mint("a1")
    # Flip the last hex digit to a DIFFERENT one. Appending a fixed "0" fails one run in
    # sixteen, when the real signature already ends in zero — a test that cries wolf
    # occasionally is worse than no test, because the next failure gets re-run instead of
    # read.
    flipped = signature[:-1] + ("1" if signature[-1] == "0" else "0")
    assert flipped != signature
    for forged in ("", "0" * 64, flipped, signature[:-1], signature.upper(), signature + "0"):
        assert not sharing.verify("a1", expires, forged), f"accepted {forged[:12]!r}"


def test_a_non_numeric_expiry_is_rejected_rather_than_crashing():
    """The query string is attacker-controlled."""
    signature, _ = sharing.mint("a1")
    for junk in ("soon", "", None, "9e99999", "inf"):
        assert not sharing.verify("a1", junk, signature)


def test_sharing_is_off_when_no_secret_is_configured(monkeypatch):
    """Fails closed. A predictable signing key is worse than no sharing at all."""
    signature, expires = sharing.mint("a1")
    monkeypatch.delenv("MEYRAKI_SHARE_SECRET", raising=False)
    assert not sharing.enabled()
    assert not sharing.verify("a1", expires, signature)
    with pytest.raises(sharing.SharingDisabled):
        sharing.mint("a1")


def test_a_different_secret_cannot_verify_our_tokens(monkeypatch):
    signature, expires = sharing.mint("a1")
    monkeypatch.setenv("MEYRAKI_SHARE_SECRET", "a-completely-different-secret")
    assert not sharing.verify("a1", expires, signature)


def test_the_ttl_is_clamped():
    """A link that never expires is a permanent credential in someone's inbox."""
    _sig, far = sharing.mint("a1", ttl_seconds=10 * 365 * 24 * 3600)
    assert far - time.time() <= sharing.MAX_TTL_SECONDS + 5
    _sig, near = sharing.mint("a1", ttl_seconds=0)
    assert near - time.time() >= 59


# ---------------------------------------------------------------- over HTTP

def test_a_shared_link_serves_the_report_without_any_session(client, analysis):
    minted = client.post(f"/analyses/{analysis}/share").json()
    assert minted["url"].startswith("http") and "sig=" in minted["url"]

    anonymous = TestClient(app)  # no cookies, no account — the whole point
    path = minted["url"].split("/shared/")[1]
    r = anonymous.get(f"/shared/{path}")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
    assert "inline" in r.headers.get("content-disposition", "")


def test_every_bad_link_looks_identical_to_an_unknown_analysis(client, analysis):
    """Expired, forged and nonexistent must be indistinguishable, or the endpoint tells
    an attacker which analyses exist."""
    anonymous = TestClient(app)
    good_sig, expires = sharing.mint(analysis)

    cases = {
        "forged signature": f"/shared/reports/{analysis}?expires={expires}&sig={'0' * 64}",
        "edited expiry": f"/shared/reports/{analysis}?expires={expires + 999}&sig={good_sig}",
        "no signature": f"/shared/reports/{analysis}",
        "unknown analysis": f"/shared/reports/does-not-exist?expires={expires}&sig={good_sig}",
    }
    for name, url in cases.items():
        r = anonymous.get(url)
        assert r.status_code == 404, f"{name} returned {r.status_code}"


def test_a_token_for_one_analysis_does_not_open_another(client, analysis):
    """The signature binds the analysis id, so swapping it in the path fails."""
    good_sig, expires = sharing.mint(analysis)
    other = TestClient(app)
    r = other.get(f"/shared/reports/{analysis[::-1]}?expires={expires}&sig={good_sig}")
    assert r.status_code == 404


def test_another_studio_cannot_mint_a_link_for_your_analysis(client, analysis):
    """Minting is org-scoped even though redeeming is not."""
    rival = TestClient(app)
    with rival as r:
        r.post("/auth/register", json={
            "email": f"rival-share-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "RivalShare",
        })
        assert r.post(f"/analyses/{analysis}/share").status_code == 404


def test_minting_without_a_secret_reports_it_rather_than_serving_a_weak_link(
    client, analysis, monkeypatch
):
    monkeypatch.delenv("MEYRAKI_SHARE_SECRET", raising=False)
    r = client.post(f"/analyses/{analysis}/share")
    assert r.status_code == 503
    assert "MEYRAKI_SHARE_SECRET" in r.json()["detail"]
