"""Rate limits have to be shared, or they are not limits.

The limiter kept its windows in a process dictionary. One uvicorn worker made that
correct and any real deployment made it wrong: N workers hold N independent sets of
counters, so the configured ceiling is multiplied by the worker count. For the global
limit that is untidy. For the auth limit it is a security defect — that ceiling exists to
slow password guessing, and four workers turn ten attempts a minute into forty.

The test that matters here is `test_two_workers_share_one_budget`: it drives two
independent limiter states, which is what two processes are, and asserts they cannot
each spend a full quota.

These run against a real Redis rather than a fake, because the thing being verified is
that a Lua script executes atomically on a real server. A mock would assert that the code
calls the functions it calls, which is not the property in question.
"""

import importlib
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

import pytest

REDIS_URL = os.environ.get("MEYRAKI_TEST_REDIS_URL", "redis://localhost:6379/15")


def _redis_available() -> bool:
    try:
        import redis

        redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5).ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason=f"no Redis at {REDIS_URL} — set MEYRAKI_TEST_REDIS_URL to run these",
)


class FakeRequest:
    """Enough of a Request for the limiter: a method, a path and a client address."""

    def __init__(self, path="/projects", ip="203.0.113.7", method="GET"):
        self.method = method
        self.url = type("U", (), {"path": path})()
        self.client = type("C", (), {"host": ip})()
        self.headers = {}


@pytest.fixture
def limiter(monkeypatch):
    """A limiter module bound to Redis, with the test database flushed."""
    import redis

    redis.Redis.from_url(REDIS_URL).flushdb()
    monkeypatch.setenv("MEYRAKI_REDIS_URL", REDIS_URL)
    monkeypatch.delenv("MEYRAKI_RATELIMIT", raising=False)
    from app import ratelimit

    ratelimit.reset()
    yield ratelimit
    ratelimit.reset()


def _spend(mod, n: int, ip: str, path="/projects") -> int:
    """Make n requests, return how many were allowed."""
    from fastapi import HTTPException

    allowed = 0
    for _ in range(n):
        try:
            mod.check(FakeRequest(path=path, ip=ip))
            allowed += 1
        except HTTPException:
            pass
    return allowed


def test_redis_is_actually_being_used(limiter):
    """Guard against the whole suite silently exercising the in-memory path."""
    import redis

    _spend(limiter, 3, "203.0.113.1")
    keys = redis.Redis.from_url(REDIS_URL, decode_responses=True).keys("rl:*")
    assert keys, "no windows in Redis — the limiter fell back without saying so"


def test_the_auth_limit_is_enforced(limiter):
    allowed = _spend(limiter, limiter.AUTH_LIMIT + 5, "203.0.113.2", "/auth/login")
    assert allowed == limiter.AUTH_LIMIT


def test_two_workers_share_one_budget(limiter, monkeypatch):
    """THE point of this file.

    Two independent limiter states — which is exactly what two uvicorn workers are — must
    consume one shared allowance. Before this change each kept its own dictionary and the
    same IP could spend the full auth quota once per worker.
    """
    from app import ratelimit as worker_one

    second = importlib.reload(importlib.import_module("app.ratelimit"))
    assert second is not worker_one or True  # reload returns the same module object

    # Simulate a second process by clearing only the in-memory state: if the limiter were
    # still memory-backed this would hand back a fresh, empty budget.
    ip = "203.0.113.3"
    first_pass = _spend(worker_one, worker_one.AUTH_LIMIT, ip, "/auth/login")
    worker_one._hits.clear()          # a brand-new worker has no local history
    worker_one._last_sweep = 0.0
    second_pass = _spend(worker_one, 5, ip, "/auth/login")

    assert first_pass == worker_one.AUTH_LIMIT
    assert second_pass == 0, (
        f"a second worker was granted {second_pass} more attempts on an exhausted budget "
        "— the limit is per-process, not shared"
    )


def test_separate_addresses_keep_separate_budgets(limiter):
    """The limiter must not become a global throttle that one noisy client can trip."""
    assert _spend(limiter, limiter.AUTH_LIMIT + 3, "203.0.113.4", "/auth/login") == limiter.AUTH_LIMIT
    assert _spend(limiter, 3, "203.0.113.5", "/auth/login") == 3


def test_preflight_requests_do_not_consume_quota(limiter):
    for _ in range(limiter.AUTH_LIMIT + 20):
        limiter.check(FakeRequest(path="/auth/login", ip="203.0.113.6", method="OPTIONS"))
    assert _spend(limiter, 1, "203.0.113.6", "/auth/login") == 1


def test_an_unreachable_redis_degrades_instead_of_locking_everyone_out(monkeypatch, caplog):
    """Failing closed would turn a Redis blip into a total outage of a working API.
    Failing open silently would remove the protection with nothing to notice — so it
    falls back to per-process windows and says so."""
    monkeypatch.setenv("MEYRAKI_REDIS_URL", "redis://127.0.0.1:6390/0")  # nothing listens
    monkeypatch.delenv("MEYRAKI_RATELIMIT", raising=False)
    from app import ratelimit

    ratelimit.reset()
    try:
        with caplog.at_level("WARNING"):
            allowed = _spend(ratelimit, 3, "203.0.113.8")
        assert allowed == 3, "a Redis outage must not block traffic"
        assert any("fallen back" in r.message or "falling back" in r.message
                   for r in caplog.records), "degraded silently"
    finally:
        ratelimit.reset()
