"""Moodboard interior renders — provider chain (docs/04-REUSE-MAP §2).

Order: Gemini 2.5 Flash Image (contracted, auto-preferred the moment its key has quota)
→ FLUX.1-schnell via Hugging Face Inference Providers (final quality, needs a token)
→ Pollinations (keyless, free, DRAFT quality) so the product is never blocked on a
billing state. First success wins; every provider failure is collected and surfaced by
the caller, never swallowed.

Measured 2026-08-06 on the same prompt: FLUX returns a true 1024x1024 in ~8s with real
material definition — walnut figure, brushed brass, travertine veining, linen weave —
where the free tier returns 768px of soft approximation. That is the difference between
a client visual and a mood indication, which is why FLUX counts as final quality and
the free tier does not.

The free tier is DRAFT quality, and the difference is visible: it caps output at ~768px
whatever we request, and it no longer serves FLUX — as of 2026-08-06 its model list is
just ["sana"], a small fast distillation whose interiors come back soft, with materials
unresolved (no travertine veining, no brass, no linen weave). Fine as a direction
indicator, not a client visual — so `renders_are_draft` travels with the Moodboard and
the report captions it. Do not quietly present these as finished work.

# ponytail: free fallback has no SLA/commercial guarantees — it is the pilot
# stopgap, not the production path. Gemini is the contracted provider.
"""

import base64
import os
import urllib.parse
from typing import Callable, NamedTuple

import httpx

from . import costs, settings

GEMINI_MODEL = "gemini-2.5-flash-image"
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
_FREE_URL = "https://image.pollinations.ai/prompt/"
# hf-inference deprecated FLUX (HTTP 410), so this goes through the router to fal-ai,
# which serves 1024x1024 as a ~300KB JPEG — a quarter the bytes of the same image from
# nscale's inline PNG, which matters when three embed into a report PDF.
#
# FLUX.1-Krea-dev over schnell, decided by looking at the output rather than the
# benchmark: Krea is tuned specifically for photorealism and returns real travertine
# aggregate, book-matched walnut grain, brushed brass and linen weave with correct light
# falloff, where schnell returns a softer approximation. It is 8x the price ($0.025 vs
# $0.003 per megapixel, both verified on fal.ai on 2026-08-07) and ~2s slower, which is
# the right trade for an image that goes in front of a paying client. Override with
# MEYRAKI_FLUX_MODEL to drop to `fal-ai/flux/schnell` when volume matters more than
# fidelity — the price table below prices whichever one is selected.
FLUX_MODEL = os.environ.get("MEYRAKI_FLUX_MODEL", "fal-ai/flux/krea")
_FLUX_URL = f"https://router.huggingface.co/fal-ai/{FLUX_MODEL}"

MIN_IMAGE_BYTES = 5_000       # below this it is an error page or a placeholder, not a render
def is_final_quality(provider: str) -> bool:
    """Contracted providers deliver client visuals; the free tier delivers direction."""
    return provider == "gemini" or provider.startswith("flux-")


NEEDS_COOLDOWN = frozenset({"pollinations"})  # only the free tier 429s on back-to-back calls
DRAFT_MIN_LONG_EDGE = 1024    # the free tier silently caps at 768 whatever we ask for
MAX_IMAGE_BYTES = 12_000_000  # above this the PDF data-URI bloat is a DoS (review M8)
# Per-read timeout must be far below the total budget so a drip-feeding host
# cannot hold a worker thread indefinitely (review M8).
TIMEOUT = httpx.Timeout(120.0, read=20.0, connect=10.0)
# The free tier needs its own, larger read window: measured fresh generations take
# ~45s (cached prompts return in under 2s), so the 20s above was aborting requests
# that would have succeeded — two of every three renders were lost to our own
# timeout. The drip-feed protection moves up to a wall-clock budget on the batch
# (RENDER_BUDGET_S) rather than being enforced by an unrealistically short read.
FREE_TIMEOUT = httpx.Timeout(150.0, read=90.0, connect=10.0)
# Measured ~8s including the result fetch; generous headroom without being unbounded.
FLUX_TIMEOUT = httpx.Timeout(120.0, read=60.0, connect=10.0)
# Measured: back-to-back requests 429; 3s and 6s gaps still 429; 10s+ got through.
FREE_COOLDOWN_S = 12.0
# Whole-batch ceiling so a slow or drip-feeding provider cannot stretch the pipeline.
RENDER_BUDGET_S = 240.0
_MAGIC = ((b"\x89PNG\r\n\x1a\n", ".png", "image/png"), (b"\xff\xd8\xff", ".jpg", "image/jpeg"))


class Render(NamedTuple):
    data: bytes
    suffix: str
    provider: str
    pixels: tuple[int, int] = (0, 0)

    @property
    def is_draft(self) -> bool:
        """Draft unless a contracted provider returned it at a usable resolution."""
        return not is_final_quality(self.provider) or max(self.pixels) < DRAFT_MIN_LONG_EDGE


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
            # Magic bytes only prove the header. A truncated JPEG passes that check and
            # then renders as a grey box in the client's PDF, so decode it for real.
            import io

            from PIL import Image

            try:
                image = Image.open(io.BytesIO(data))
                image.load()
            except Exception as exc:
                raise RuntimeError(f"{provider} returned an undecodable image ({exc})") from exc
            return Render(data, suffix, provider, image.size)
    raise RuntimeError(f"{provider} returned a non-image payload")


def describe_resolution(renders: list[Render]) -> str:
    """" at 1024px" / " at 768–1024px" / "" — for the pipeline register.

    A mixed batch happens whenever a paid provider runs out of credits partway through,
    and reporting only its best resolution reads as though every render came back full
    size. The range is the honest summary.
    """
    edges = sorted({max(r.pixels) for r in renders if max(r.pixels)})
    if not edges:
        return ""
    if len(edges) == 1:
        return f" at {edges[0]}px"
    return f" at {edges[0]}\u2013{edges[-1]}px"


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
    # Booked here, not after _classify: the image is paid for once the call succeeds,
    # so a payload we then reject still cost money.
    costs.record_image("moodboard", "gemini")
    return _classify(extract_image(response.json()), "gemini")


def _billable_units(headers) -> float:
    """What fal says it charged, defaulting to one unit when it says nothing usable.

    A trust boundary, and it bit twice: `float("1,5")` from a locale-formatted upstream
    raised out of the provider before the charge was booked, so the chain fell through to
    the free tier — the client got a DRAFT for a 1024px render we had already paid for,
    and the receipt said $0.00. `inf` was worse: it tripped the analysis budget and
    aborted a paying client's report after the renders had succeeded.
    """
    import math

    raw = headers.get("x-fal-billable-units")
    try:
        units = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(units) or units <= 0:
        return 1.0
    return units


def _flux_provider() -> str:
    """Provider label that names the model, so the receipt says which one was billed."""
    return "flux-" + FLUX_MODEL.rsplit("/", 1)[-1]


def _flux(prompt: str) -> Render:
    """FLUX.1-schnell through Hugging Face Inference Providers."""
    if not settings.HUGGINGFACE_API_TOKEN:
        raise RuntimeError("Hugging Face token not configured")
    response = httpx.post(
        _FLUX_URL,
        headers={
            "authorization": f"Bearer {settings.HUGGINGFACE_API_TOKEN}",
            "content-type": "application/json",
        },
        json={"prompt": prompt, "image_size": "square_hd"},
        timeout=FLUX_TIMEOUT,
    )
    response.raise_for_status()
    # fal reports what it actually charged. Billing from that beats assuming one image
    # is one charge: it is priced per megapixel, so a larger render costs proportionally
    # more and the receipt still reconciles.
    costs.record_image("moodboard", _flux_provider(), _billable_units(response.headers))
    try:
        images = response.json().get("images") or []
    except ValueError as exc:  # 200 with a non-JSON body
        raise RuntimeError(f"FLUX returned an unreadable response ({exc})") from exc
    if not images or not images[0].get("url"):
        raise RuntimeError("FLUX returned no image url")
    # The generation is already paid for by this point, so say plainly when it is the
    # delivery that failed rather than the generation — otherwise a bad media host looks
    # like a model problem and sends whoever reads the note to the wrong place.
    try:
        fetched = httpx.get(images[0]["url"], timeout=FLUX_TIMEOUT, follow_redirects=True)
        fetched.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"FLUX generated an image but its delivery failed ({type(exc).__name__})"
        ) from exc
    return _classify(fetched.content, _flux_provider())


def _free(prompt: str) -> Render:
    # safe="" — a model/user-influenced prompt must never inject path segments
    # into the provider URL (review M7).
    url = _FREE_URL + urllib.parse.quote(prompt[:900], safe="")
    response = httpx.get(
        url,
        params={"width": 1024, "height": 1024, "nologo": "true"},
        timeout=FREE_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    costs.record_image("moodboard", "pollinations")  # free, but it belongs in the receipt
    return _classify(response.content, "pollinations")


PROVIDERS: list[Callable[[str], Render]] = [_gemini, _flux, _free]


def generate_render(prompt: str) -> Render:
    errors: list[str] = []
    for provider in PROVIDERS:
        try:
            return provider(prompt)
        except Exception as exc:  # noqa: BLE001 — collected; all-failed raises below
            errors.append(f"{provider.__name__.strip('_')}: {str(exc)[:120]}")
    raise RuntimeError("; ".join(errors) or "no image provider available")
