"""Tying a delivered report to the code that produced it.

The audit trail recorded every step's output, its error, its model id and its spend — but
not which build made the decisions. The numbers a client is most likely to query months
later are the ones the agent prompts move, and prompts are ordinary source files, so a
report written before a prompt change was indistinguishable from one written after.

`CONTRACT_VERSION` does not close that gap: it versions the shape of the contracts, not
the prompts, and it barely moves. The commit sha does.

The rule this file pins is that provenance never becomes a failure mode. Not knowing the
build is a gap in an audit trail; refusing to run an analysis because a build argument was
not passed would be a far worse outcome than "unknown".
"""

import os
import subprocess

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest
from fastapi.testclient import TestClient

from app import version
from app.db import SessionLocal
from app.main import app
from app.models import Analysis

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def _clear_cache():
    version.build_version.cache_clear()
    yield
    version.build_version.cache_clear()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/auth/register", json={
            "email": f"build-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "BuildOrg",
        })
        yield c


def test_a_baked_sha_wins(monkeypatch):
    """What a container has: no git, one environment variable."""
    monkeypatch.setenv("MEYRAKI_GIT_SHA", "abc1234")
    assert version.build_version() == "abc1234"


def test_it_falls_back_to_git_outside_a_container(monkeypatch):
    """So the stamp is useful in development without anyone remembering a build flag."""
    monkeypatch.delenv("MEYRAKI_GIT_SHA", raising=False)
    resolved = version.build_version()
    assert resolved != version.UNKNOWN
    actual = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert resolved == actual


def test_an_absent_git_reports_unknown_rather_than_raising(monkeypatch):
    """Provenance must never be able to take down an API process."""
    monkeypatch.delenv("MEYRAKI_GIT_SHA", raising=False)

    def explode(*_a, **_kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", explode)
    assert version.build_version() == version.UNKNOWN


def test_a_failed_git_call_reports_unknown(monkeypatch):
    """Built from a tarball with no .git directory — a normal way to deploy."""
    monkeypatch.delenv("MEYRAKI_GIT_SHA", raising=False)

    class Failed:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: Failed())
    assert version.build_version() == version.UNKNOWN


def test_an_empty_build_arg_is_treated_as_absent(monkeypatch):
    """`--build-arg GIT_SHA=` sets the variable to an empty string, which must not be
    stamped onto an analysis as though it were a real sha."""
    monkeypatch.setenv("MEYRAKI_GIT_SHA", "   ")
    assert version.build_version() != "   "


def test_health_reports_which_build_is_serving(client):
    """The first question of any incident, answerable without shell access."""
    body = client.get("/health").json()
    assert body["build"]
    assert body["build"] == version.build_version()


def test_an_analysis_records_the_build_that_ran_it(client):
    """The point of the whole file: a delivered report traces back to its code."""
    project_id = client.post(
        "/projects", json={"name": "Build stamp", "space_type": "hotel"}
    ).json()["id"]
    upload = client.post(
        f"/projects/{project_id}/uploads", params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    aid = client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": upload["id"], "objectives": ["guest_flow"]},
    ).json()["id"]

    with SessionLocal() as session:
        stamped = session.get(Analysis, aid).app_version
    assert stamped == version.build_version()
    assert stamped != version.UNKNOWN
