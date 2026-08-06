"""Moodboard interior renders — Gemini 2.5 Flash Image (docs/04-REUSE-MAP §2).

Called via REST with httpx (already a dependency) — no SDK needed for one endpoint.
"""

import base64

import httpx

from . import settings

GEMINI_MODEL = "gemini-2.5-flash-image"
_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)


def enabled() -> bool:
    return bool(settings.GEMINI_API_KEY)


def extract_image(data: dict) -> bytes:
    """First inline image from a generateContent response; loud failure otherwise."""
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


def generate_render(prompt: str) -> bytes:
    response = httpx.post(
        _URL,
        headers={
            "x-goog-api-key": settings.GEMINI_API_KEY,
            "content-type": "application/json",
        },
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=120,
    )
    response.raise_for_status()
    return extract_image(response.json())
