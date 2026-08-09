"""Rate limiting, shared across workers when Redis is configured (M5, M6).

The original limiter kept its windows in a process dictionary. That is correct for one
uvicorn worker and quietly wrong for any real deployment: with N workers each holds its
own counters, so the effective limit is N times the configured one. The global ceiling
being loose is untidy; the auth ceiling being loose is a security defect, because that
one exists to slow password guessing, and four workers turn ten attempts a minute into
forty.

So when MEYRAKI_REDIS_URL is set the windows live in Redis and every worker — every
container — shares them. Without it, the in-memory limiter still runs, which keeps
development and the test suite free of an infrastructure dependency.

Both backends implement the same sliding window, deliberately. A fixed-window counter is
simpler in Redis, but it lets a caller spend a full quota at the end of one window and
another at the start of the next, which doubles the burst precisely at the boundary an
attacker would aim for. Keeping one algorithm also means the tests pin real behaviour
rather than whichever backend happened to be configured.

If Redis is configured but unreachable, this degrades to the in-memory limiter and says
so, once. The alternative — failing closed — turns a Redis blip into a total outage of a
working API, and failing open silently would remove the protection with nothing to notice.
"""

import logging
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

log = logging.getLogger(__name__)

WINDOW_SECONDS = 60
AUTH_LIMIT = 10      # login/register attempts per IP per minute
GLOBAL_LIMIT = 300   # any requests per IP per minute

_hits: dict[str, deque] = defaultdict(deque)
_last_sweep = 0.0

_redis = None
_redis_failed = False

# One round trip, and atomic: without this the read and the write race, and enough
# simultaneous requests slip past a limit they should have hit. Trims the window, counts
# what remains, and only then records the hit.
_SLIDING_WINDOW_LUA = """
local key, now, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local used = redis.call('ZCARD', key)
if used >= limit then
  return 1
end
redis.call('ZADD', key, now, ARGV[4])
redis.call('EXPIRE', key, window)
return 0
"""


def _client():
    """The Redis client, or None to use the in-process windows."""
    global _redis, _redis_failed
    url = os.environ.get("MEYRAKI_REDIS_URL", "").strip()
    if not url or _redis_failed:
        return None
    if _redis is None:
        try:
            import redis

            _redis = redis.Redis.from_url(
                url, socket_timeout=0.25, socket_connect_timeout=0.25,
                decode_responses=True,
            )
            _redis.ping()
        except Exception as exc:  # noqa: BLE001 — any client or network failure
            _redis, _redis_failed = None, True
            log.warning(
                "MEYRAKI_REDIS_URL is set but Redis is unreachable (%s); rate limiting "
                "has fallen back to per-process windows, so limits are multiplied by the "
                "worker count until this is fixed.", exc,
            )
            return None
    return _redis


def client_ip(request: Request) -> str:
    """Behind a proxy every user presents the proxy IP and shares one bucket
    (review M5). Set MEYRAKI_TRUST_PROXY=1 only when a trusted proxy is the
    sole ingress; then the last X-Forwarded-For hop is the real client.
    """
    if os.environ.get("MEYRAKI_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _sweep(now: float) -> None:
    """Evict idle IPs so _hits can't grow without bound (review M6)."""
    global _last_sweep
    if now - _last_sweep < WINDOW_SECONDS:
        return
    _last_sweep = now
    for key in [k for k, w in _hits.items() if not w or now - w[-1] > WINDOW_SECONDS]:
        del _hits[key]


def _over_limit_memory(key: str, limit: int, now: float) -> bool:
    window = _hits[key]
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    if len(window) >= limit:
        return True
    window.append(now)
    return False


def _over_limit_redis(client, key: str, limit: int, now: float) -> bool | None:
    """True/False, or None when Redis failed and the caller should fall back."""
    global _redis, _redis_failed
    try:
        # The member must be unique per hit or ZADD overwrites the previous score and two
        # requests in the same instant count once. now plus a counter is enough.
        member = f"{now:.6f}-{os.getpid()}-{_next_seq()}"
        return bool(client.eval(_SLIDING_WINDOW_LUA, 1, key, now, WINDOW_SECONDS, limit, member))
    except Exception as exc:  # noqa: BLE001
        _redis, _redis_failed = None, True
        log.warning("Redis rate limiting failed mid-request (%s); falling back to "
                    "per-process windows.", exc)
        return None


_seq = 0


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


def check(request: Request) -> None:
    if os.environ.get("MEYRAKI_RATELIMIT") == "off":  # test suites
        return
    if request.method == "OPTIONS":  # preflights must not consume quota (review m9)
        return
    ip = client_ip(request)
    path = request.url.path
    buckets = [(f"g:{ip}", GLOBAL_LIMIT)]
    if path.startswith("/auth/login") or path.startswith("/auth/register"):
        buckets.append((f"a:{ip}", AUTH_LIMIT))

    client = _client()
    # Wall-clock for Redis so every worker agrees on "now"; monotonic in memory because a
    # single process only needs a clock that cannot jump backwards.
    now = time.time() if client else time.monotonic()
    if not client:
        _sweep(now)

    for key, limit in buckets:
        over = _over_limit_redis(client, f"rl:{key}", limit, now) if client else None
        if over is None:  # no Redis, or it just failed
            over = _over_limit_memory(key, limit, time.monotonic())
        if over:
            raise HTTPException(
                429,
                "Too many requests — slow down and try again in a minute.",
                headers={"Retry-After": "60"},
            )


def reset() -> None:
    """Test hook."""
    global _last_sweep, _redis, _redis_failed
    _hits.clear()
    _last_sweep = 0.0
    _redis, _redis_failed = None, False
