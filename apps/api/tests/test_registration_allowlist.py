"""A public URL means strangers can sign up and spend the owner's model credits.

The deployment went live behind a Cloudflare Tunnel on a real domain, and signup was
open — as it had always been, correctly, for a stack only ever reached on localhost. On a
public URL that same default hands anyone who finds the address an account, and every
analysis they run costs the owner roughly $0.25 of real model spend.

`MEYRAKI_ALLOWED_EMAILS` closes registration to a named list. Two properties matter more
than the check itself:

- **Login is never gated.** Only registration consults the list, so switching it on later
  cannot lock an existing user out of data they already own. A gate that strands its own
  users is worse than an open door.
- **Unset means open.** Development and the test suite must not need configuration to
  work, and a deployment that silently refused every signup would look broken rather than
  protected.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest
from fastapi.testclient import TestClient

from app import settings
from app.main import app


# Patch the parsed value rather than the environment variable.
#
# The first version of this file reloaded the settings module to re-read the variable,
# which re-ran its dotenv loader and pulled the DEPLOYMENT's real allowlist out of
# apps/api/.env — poisoning global state for every test that ran afterwards and failing
# eleven of them in modules that have nothing to do with registration. Reloading a
# settings module is a blunt instrument in a suite that shares one process; monkeypatch
# is surgical and reverts itself.


@pytest.fixture
def allowlisted(monkeypatch):
    monkeypatch.setattr(
        settings, "ALLOWED_EMAILS", frozenset({"owner@example.com", "second@example.com"})
    )


@pytest.fixture
def open_signup(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_EMAILS", frozenset())


def test_unset_keeps_signup_open(open_signup):
    """Development must work with no configuration at all."""
    assert settings.registration_allowed("anyone@example.com")


def test_a_listed_address_may_register(allowlisted):
    assert settings.registration_allowed("owner@example.com")


def test_an_unlisted_address_may_not(allowlisted):
    assert not settings.registration_allowed("stranger@example.com")


@pytest.mark.parametrize("written", [
    "OWNER@EXAMPLE.COM",       # a mail client capitalised it
    "  owner@example.com  ",   # copied with whitespace
    "Owner@Example.com",
])
def test_the_address_is_matched_case_and_space_insensitively(allowlisted, written):
    """Email case is not significant, and a user who cannot sign in because they typed a
    capital would report it as 'the site is broken'."""
    assert settings.registration_allowed(written)


def test_the_endpoint_refuses_an_unlisted_signup(allowlisted):
    with TestClient(app) as c:
        r = c.post("/auth/register", json={
            "email": "stranger@example.com",
            "password": "test-password-1",
            "org_name": "Uninvited",
        })
    assert r.status_code == 403
    assert "invitation-only" in r.json()["detail"].lower()


def test_an_existing_user_can_still_sign_in_after_the_gate_closes(monkeypatch):
    """THE property worth protecting: turning the allowlist on must not strand anyone.

    Registration happens while signup is open, then the deployment is locked down — which
    is exactly the order it happens in real life.
    """
    email = f"existing-{os.getpid()}@example.com"
    monkeypatch.setattr(settings, "ALLOWED_EMAILS", frozenset())
    with TestClient(app) as c:
        assert c.post("/auth/register", json={
            "email": email, "password": "test-password-1", "org_name": "Early",
        }).status_code == 201

    # the gate closes, excluding them
    monkeypatch.setattr(settings, "ALLOWED_EMAILS", frozenset({"someone-else@example.com"}))
    with TestClient(app) as c:
        assert c.post("/auth/login", json={
            "email": email, "password": "test-password-1",
        }).status_code == 200, "an existing user was locked out by the allowlist"
