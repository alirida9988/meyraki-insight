"""In-app rate limiting (M5).
# ponytail: per-process in-memory windows — move to Redis when workers scale out.
"""

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

WINDOW_SECONDS = 60
AUTH_LIMIT = 10      # login/register attempts per IP per minute
GLOBAL_LIMIT = 300   # any requests per IP per minute

_hits: dict[str, deque] = defaultdict(deque)
_last_sweep = 0.0


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


def check(request: Request) -> None:
    if os.environ.get("MEYRAKI_RATELIMIT") == "off":  # test suites
        return
    if request.method == "OPTIONS":  # preflights must not consume quota (review m9)
        return
    now = time.monotonic()
    _sweep(now)
    ip = client_ip(request)
    path = request.url.path
    buckets = [(f"g:{ip}", GLOBAL_LIMIT)]
    if path.startswith("/auth/login") or path.startswith("/auth/register"):
        buckets.append((f"a:{ip}", AUTH_LIMIT))
    for key, limit in buckets:
        window = _hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(
                429,
                "Too many requests — slow down and try again in a minute.",
                headers={"Retry-After": "60"},
            )
        window.append(now)


def reset() -> None:
    """Test hook."""
    global _last_sweep
    _hits.clear()
    _last_sweep = 0.0
