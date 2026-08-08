"""Signed, expiring links for the client-facing report (M5 security pass).

A studio finishes an analysis and wants their client to read the report. The client has
no account and should never get one — so the link has to carry its own authority, and
carry as little of it as possible: one analysis, one document, one deadline.

Design decisions worth stating, because each is a way this could have gone wrong:

- **Fails closed.** With no `MEYRAKI_SHARE_SECRET` configured, sharing is disabled rather
  than falling back to some derived value. A predictable signing key is worse than no
  sharing at all, and a deployment that silently invents one is a deployment nobody
  audited.
- **The signature covers the expiry**, so pushing the deadline out invalidates the token
  instead of extending it.
- **Scoped to one analysis.** The token proves nothing about any other analysis, any
  other artifact, or the organisation that owns it — a leaked link exposes exactly the
  document it was minted for.
- **Constant-time comparison**, because signature checks are guessable one byte at a time
  otherwise.
- **No session, no org lookup, no cookie.** The link is the whole credential, which is
  precisely why it expires.
"""

import hashlib
import hmac
import os
import time

PURPOSE = "report"
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
MAX_TTL_SECONDS = 30 * 24 * 3600


class SharingDisabled(RuntimeError):
    """No signing secret configured — the feature is off rather than weakly on."""


def secret() -> str:
    return os.environ.get("MEYRAKI_SHARE_SECRET", "")


def enabled() -> bool:
    return bool(secret())


def _signature(analysis_id: str, expires_at: int) -> str:
    # The purpose string is in the payload so a token minted for the report can never be
    # replayed against a future endpoint that signs the same fields.
    payload = f"{PURPOSE}:{analysis_id}:{expires_at}".encode()
    return hmac.new(secret().encode(), payload, hashlib.sha256).hexdigest()


def mint(analysis_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> tuple[str, int]:
    """(signature, expires_at) for a link to one analysis's report."""
    if not enabled():
        raise SharingDisabled(
            "MEYRAKI_SHARE_SECRET is not set — report sharing is disabled. Set it to a "
            "long random value; a predictable signing key is worse than no sharing."
        )
    ttl = max(60, min(int(ttl_seconds), MAX_TTL_SECONDS))
    expires_at = int(time.time()) + ttl
    return _signature(analysis_id, expires_at), expires_at


def verify(analysis_id: str, expires_at: int, signature: str) -> bool:
    """True only for an untampered, unexpired token for exactly this analysis."""
    if not enabled() or not signature:
        return False
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        return False
    if expires_at < time.time():
        return False
    return hmac.compare_digest(_signature(analysis_id, expires_at), signature)
