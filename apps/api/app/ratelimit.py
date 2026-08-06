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


def check(request: Request) -> None:
    if os.environ.get("MEYRAKI_RATELIMIT") == "off":  # test suites
        return
    ip = request.client.host if request.client else "unknown"
    path = request.url.path
    now = time.monotonic()
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
    _hits.clear()
