"""Moodboard interior renders — provider chain (docs/04-REUSE-MAP §2).

Order: Gemini 2.5 Flash Image (paid, best quality, auto-preferred the moment the
key has quota) → Pollinations/FLUX (keyless, free) so the product is never
blocked on a billing state. First success wins; every provider failure is
collected and surfaced by the caller, never swallowed.

# ponytail: free fallback has no SLA/commercial guarantees — it is the pilot
# stopgap, not the production path. Gemini is the contracted provider.
"""

import base64
import urllib.parse
from typing import Callable, NamedTuple

import httpx

from . import settings

GEMINI_MODEL = "gemini-2.5-flash-image"
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
_FREE_URL = "https://image.pollinations.ai/prompt/"

MIN_IMAGE_BYTES = 5_000       # below this it is an error page or a placeholder, not a render
MAX_IMAGE_BYTES = 12_000_000  # above this the PDF data-URI bloat is a DoS (review M8)
# Per-read timeout must be far below the total budget so a drip-feeding host
# cannot hold a worker thread indefinitely (review M8).
TIMEOUT = httpx.Timeout(120.0, read=20.0, connect=10.0)
_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png", "image/png"), (b"\xff\xd8\xff", ".jpg", "image/jpeg"))


class Render(NamedTuple):
    data: bytes
    suffix: str
    provider: str


def media_type(data: bytes) -> str:
    """Mime from magic bytes — never trust an extension for embedding."""
    for magic, _suffix, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    return "application/octet-stream"


def _classify(data: bytes, provider: str) -> Render:
    for magic, suffix, _mime in _MAGIC:
        if data.startswith(magic):
            if len(data) < MIN_IMAGE_BYTES:
                raise RuntimeError(f"{provider} returned a {len(data)}-byte image (too small)")
            if len(data) > MAX_IMAGE_BYTES:
                raise RuntimeError(f"{provider} returned {len(data)} bytes (over the size cap)")
            return Render(data, suffix, provider)
    raise RuntimeError(f"{provider} returned a non-image payload")


def enabled() -> bool:
    """A render provider is always available (the free one needs no key)."""
    return True


def extract_image(data: dict) -> bytes:
    """First inline image from a Gemini generateContent response; loud otherwise."""
    candidates = data.get("candidates") or [{}]
    parts = candidates[0].get("content", {}).get("parts", [])
    for part in parts:
        blob = part.get("inlineData") or part.get("inline_data")
        if blob and blob.get("data"):
            # validate=True: malformed base64 must fail loudly, never silently
            # produce an empty/corrupt image file (review F5).
            image = base64.b64decode(blob["data"], validate=True)
            if not image:
                raise RuntimeError("Gemini returned an empty image payload")
            return image
    reason = candidates[0].get("finishReason", "unknown")
    raise RuntimeError(f"Gemini returned no image (finishReason: {reason})")


def _gemini(prompt: str) -> Render:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("Gemini key not configured")
    response = httpx.post(
        _GEMINI_URL,
        headers={"x-goog-api-key": settings.GEMINI_API_KEY, "content-type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return _classify(extract_image(response.json()), "gemini")


def _free(prompt: str) -> Render:
    # safe="" — a model/user-influenced prompt must never inject path segments
    # into the provider URL (review M7).
    url = _FREE_URL + urllib.parse.quote(prompt[:900], safe="")
    response = httpx.get(
        url,
        params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"},
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    return _classify(response.content, "pollinations")


PROVIDERS: list[Callable[[str], Render]] = [_gemini, _free]


def generate_render(prompt: str) -> Render:
    errors: list[str] = []
    for provider in PROVIDERS:
        try:
            return provider(prompt)
        except Exception as exc:  # noqa: BLE001 — collected; all-failed raises below
            errors.append(f"{provider.__name__.strip('_')}: {str(exc)[:120]}")
    raise RuntimeError("; ".join(errors) or "no image provider available")
