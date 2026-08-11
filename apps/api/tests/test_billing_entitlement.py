"""Who may run an analysis, and what happens when they may not.

An analysis is the unit billed because it is the unit that costs — roughly $0.25 of model
spend — so the meter and the expense line move together instead of drifting apart.

The properties worth protecting are not the arithmetic:

- **Off by default.** A half-configured paywall that starts refusing analyses during a
  client demo is a self-inflicted outage, and a pilot has no business being metered.
- **A refusal names the reason and the remedy.** "Payment required" tells a studio
  nothing; it needs to know it has used its included analyses and what to do next.
- **A paid credit is never spent on an analysis that did not start**, and never spent
  while a subscription already covers it. Those are the two billing bugs a customer would
  not forgive.
"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest
from fastapi.testclient import TestClient

from app import billing
from app.db import SessionLocal
from app.main import app
from app.models import Org, User
from conftest import VALID_PNG


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("MEYRAKI_BILLING", "on")
    monkeypatch.setenv("MEYRAKI_FREE_ANALYSES", "2")


def _evaluate(**kw):
    base = dict(plan="free", subscription_status=None, credits=0, analyses_used=0)
    return billing.evaluate(**{**base, **kw})


# ---------------------------------------------------------------- the default

def test_billing_is_off_unless_switched_on(monkeypatch):
    """The pilot must keep working with no billing configuration at all."""
    monkeypatch.delenv("MEYRAKI_BILLING", raising=False)
    assert not billing.enabled()
    e = _evaluate(analyses_used=10_000)
    assert e.allowed
    assert "not enabled" in e.reason


def test_a_typo_in_the_allowance_does_not_lock_everyone_out(monkeypatch, enforced):
    """`MEYRAKI_FREE_ANALYSES=three` must not be read as zero — that would refuse every
    organisation access to a product they are entitled to use."""
    monkeypatch.setenv("MEYRAKI_FREE_ANALYSES", "three")
    assert billing.free_allowance() == billing.DEFAULT_FREE_ANALYSES


# ---------------------------------------------------------------- the allowance

def test_the_included_analyses_are_allowed(enforced):
    assert _evaluate(analyses_used=0).allowed
    assert _evaluate(analyses_used=1).allowed


def test_the_refusal_says_what_to_do_next(enforced):
    e = _evaluate(analyses_used=2)
    assert not e.allowed
    assert "2 included analyses" in e.reason
    assert "subscribe" in e.reason.lower()


def test_an_active_subscription_is_unlimited(enforced):
    e = _evaluate(plan="studio", subscription_status="active", analyses_used=500)
    assert e.allowed


def test_a_trialing_subscription_counts_as_active(enforced):
    assert _evaluate(plan="studio", subscription_status="trialing", analyses_used=99).allowed


def test_a_failed_payment_says_so_rather_than_blaming_the_allowance(enforced):
    """A studio that believes it is paying must not be told it used up a free tier."""
    e = _evaluate(plan="studio", subscription_status="past_due", analyses_used=50)
    assert not e.allowed
    assert "payment" in e.reason.lower()
    assert "included analyses" not in e.reason


def test_a_purchased_credit_allows_one_more(enforced):
    e = _evaluate(analyses_used=2, credits=1)
    assert e.allowed
    assert "credit" in e.reason.lower()


# ---------------------------------------------------------------- spending credits

def test_a_credit_is_spent_only_when_it_is_what_permitted_the_run(enforced):
    assert billing.consumes_credit(_evaluate(analyses_used=2, credits=1))


def test_a_credit_is_not_spent_while_the_free_allowance_remains(enforced):
    assert not billing.consumes_credit(_evaluate(analyses_used=0, credits=5))


def test_a_credit_is_not_spent_while_a_subscription_covers_it(enforced):
    """Charging a studio twice for one analysis is the bug that loses the account."""
    e = _evaluate(plan="studio", subscription_status="active", analyses_used=99, credits=5)
    assert not billing.consumes_credit(e)


def test_no_credit_is_spent_when_billing_is_off(monkeypatch):
    monkeypatch.delenv("MEYRAKI_BILLING", raising=False)
    assert not billing.consumes_credit(_evaluate(analyses_used=99, credits=5))


# ---------------------------------------------------------------- over HTTP

@pytest.fixture
def client(request):
    """A fresh organisation per test.

    The first version reused one email across tests, so the second registration returned
    409, the client carried no session, and the failure surfaced as a KeyError on a
    response body rather than as "you are not signed in". Isolated orgs also mean the
    analysis count one test creates cannot change what another test is entitled to.
    """
    email = f"billing-{os.getpid()}-{request.node.name[:40]}@test.dev"
    with TestClient(app) as c:
        r = c.post("/auth/register", json={
            "email": email,
            "password": "test-password-1",
            "org_name": "BillingOrg",
        })
        assert r.status_code == 201, f"could not register the test org: {r.status_code}"
        c.email = email  # type: ignore[attr-defined]
        yield c


def test_the_billing_endpoint_reports_the_state(client):
    body = client.get("/billing").json()
    assert body["plan"] == "free"
    assert body["analyses_used"] == 0
    assert body["can_start_analysis"] is True
    assert "enforced" in body


def test_an_exhausted_allowance_refuses_the_analysis_with_402(client, enforced, monkeypatch):
    """402, not 403: this is not a permission the studio lacks, it is a payment the
    account has not made."""
    monkeypatch.setenv("MEYRAKI_FREE_ANALYSES", "0")
    pid = client.post("/projects", json={"name": "Paywall", "space_type": "cafe"}).json()["id"]
    up = client.post(f"/projects/{pid}/uploads", params={"kind": "floorplan"},
                     files={"file": ("p.png", VALID_PNG, "image/png")}).json()
    r = client.post(f"/projects/{pid}/analyses", json={
        "floorplan_upload_id": up["id"], "objectives": ["guest_flow"]})
    assert r.status_code == 402
    assert "subscribe" in r.json()["detail"].lower()


def test_a_credit_is_decremented_once_the_analysis_exists(client, enforced, monkeypatch):
    monkeypatch.setenv("MEYRAKI_FREE_ANALYSES", "0")
    with SessionLocal() as s:
        user = s.query(User).filter(User.email == client.email).first()  # type: ignore[attr-defined]
        org = s.get(Org, user.org_id)
        org.analysis_credits = 1
        s.commit()
        org_id = org.id

    pid = client.post("/projects", json={"name": "Credit", "space_type": "cafe"}).json()["id"]
    up = client.post(f"/projects/{pid}/uploads", params={"kind": "floorplan"},
                     files={"file": ("p.png", VALID_PNG, "image/png")}).json()
    r = client.post(f"/projects/{pid}/analyses", json={
        "floorplan_upload_id": up["id"], "objectives": ["guest_flow"]})
    assert r.status_code == 201

    with SessionLocal() as s:
        assert s.get(Org, org_id).analysis_credits == 0, "the credit was not spent"
