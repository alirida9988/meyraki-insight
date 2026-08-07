"""Render provider chain — parsing, classification, fallback, failure policy."""

import base64

import pytest

from app import agents, imagegen
from app.agents import WireZoneGraph, repair_zone_graph
from app.imagegen import Render, _classify, extract_image, generate_render, media_type
from tests.test_agents import _wz

def _image(fmt: str, size: tuple[int, int] = (1200, 1200)) -> bytes:
    """A real decodable image. Magic-byte-only stubs used to pass here, which is the
    exact bug _classify now catches: a truncated file with a valid header renders as a
    grey box in the client's PDF. Noise keeps it over MIN_IMAGE_BYTES after compression."""
    import io
    import random

    from PIL import Image

    rng = random.Random(7)
    img = Image.new("RGB", size)
    img.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                 for _ in range(size[0] * size[1])])
    buffer = io.BytesIO()
    img.save(buffer, format=fmt)
    return buffer.getvalue()


PNG = _image("PNG")
JPG = _image("JPEG")
B64 = base64.b64encode(PNG).decode()


# ---------------------------------------------------------------- Gemini parsing

def test_extract_image_camel_and_snake_case():
    camel = {"candidates": [{"content": {"parts": [{"inlineData": {"data": B64}}]}}]}
    snake = {"candidates": [{"content": {"parts": [{"text": "x"}, {"inline_data": {"data": B64}}]}}]}
    assert extract_image(camel) == PNG
    assert extract_image(snake) == PNG


def test_extract_image_no_image_raises_with_reason():
    data = {"candidates": [{"content": {"parts": [{"text": "refused"}]}, "finishReason": "SAFETY"}]}
    with pytest.raises(RuntimeError, match="SAFETY"):
        extract_image(data)


def test_extract_image_malformed_base64_raises():
    bad = {"candidates": [{"content": {"parts": [{"inlineData": {"data": "!!!not-base64!!!"}}]}}]}
    with pytest.raises(Exception):
        extract_image(bad)


# ---------------------------------------------------------------- classification

def test_classify_detects_png_and_jpeg():
    assert _classify(PNG, "p").suffix == ".png"
    assert _classify(JPG, "p").suffix == ".jpg"
    assert media_type(PNG) == "image/png"
    assert media_type(JPG) == "image/jpeg"
    assert media_type(b"garbage") == "application/octet-stream"


def test_classify_rejects_tiny_and_non_image_payloads():
    with pytest.raises(RuntimeError, match="too small"):
        _classify(b"\x89PNG\r\n\x1a\n" + b"0" * 10, "p")
    with pytest.raises(RuntimeError, match="non-image"):
        _classify(b"<html>error</html>", "p")


# ---------------------------------------------------------------- provider chain

def test_chain_falls_back_to_free_when_gemini_fails(monkeypatch):
    calls = []

    def gemini(prompt):
        calls.append("gemini")
        raise RuntimeError("429 quota exceeded")

    def free(prompt):
        calls.append("free")
        return Render(JPG, ".jpg", "pollinations")

    monkeypatch.setattr(imagegen, "PROVIDERS", [gemini, free])
    render = generate_render("a lobby")
    assert render.provider == "pollinations" and render.suffix == ".jpg"
    assert calls == ["gemini", "free"]


def test_chain_prefers_first_provider_and_skips_rest(monkeypatch):
    called = []
    monkeypatch.setattr(
        imagegen,
        "PROVIDERS",
        [lambda p: Render(PNG, ".png", "gemini"), lambda p: called.append("free")],
    )
    assert generate_render("x").provider == "gemini"
    assert called == []


def test_chain_all_providers_failing_raises_with_every_reason(monkeypatch):
    def boom_a(prompt):
        raise RuntimeError("gemini down")

    def boom_b(prompt):
        raise RuntimeError("free down")

    monkeypatch.setattr(imagegen, "PROVIDERS", [boom_a, boom_b])
    with pytest.raises(RuntimeError) as exc:
        generate_render("x")
    assert "gemini down" in str(exc.value) and "free down" in str(exc.value)


def test_flux_provider_requires_a_token(monkeypatch):
    from app import settings

    monkeypatch.setattr(settings, "HUGGINGFACE_API_TOKEN", "")
    with pytest.raises(RuntimeError, match="not configured"):
        imagegen._flux("x")


def test_flux_counts_as_final_quality_and_needs_no_cooldown():
    """FLUX returns a true 1024px render with real material definition, so it is not
    captioned as draft — and being a paid provider it does not rate-limit us."""
    provider = imagegen._flux_provider()
    assert imagegen.is_final_quality(provider)
    assert provider not in imagegen.NEEDS_COOLDOWN
    assert not Render(JPG, ".jpg", provider, (1024, 1024)).is_draft
    # ...but an under-resolution result from any provider is still draft
    assert Render(JPG, ".jpg", provider, (512, 512)).is_draft


def test_flux_is_tried_before_the_free_tier(monkeypatch):
    """Order matters: the free tier is the last resort, not the second choice."""
    names = [p.__name__ for p in imagegen.PROVIDERS]
    assert names.index("_flux") < names.index("_free")
    assert names.index("_gemini") < names.index("_flux")


def test_flux_reports_a_missing_image_url_instead_of_crashing(monkeypatch):
    class _Response:
        status_code = 200
        headers: dict = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"images": []}

    from app import settings

    monkeypatch.setattr(settings, "HUGGINGFACE_API_TOKEN", "hf_test")
    monkeypatch.setattr(imagegen.httpx, "post", lambda *a, **k: _Response())
    with pytest.raises(RuntimeError, match="no image url"):
        imagegen._flux("x")


def test_gemini_provider_requires_key(monkeypatch):
    from app import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    with pytest.raises(RuntimeError, match="not configured"):
        imagegen._gemini("x")


# ---------------------------------------------------------------- moodboard batch

def _graph():
    return repair_zone_graph(
        WireZoneGraph(zones=[_wz("lobby")], adjacency=[], entrances=["lobby"])
    )


def test_moodboard_images_partial_success_kept(monkeypatch, tmp_path):
    from app import settings, storage

    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    outcomes = iter([
        Render(PNG, ".png", "gemini", (1200, 1200)),
        RuntimeError("boom"),
        Render(JPG, ".jpg", "pollinations", (768, 768)),
    ])

    def fake_render(prompt):
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(imagegen, "generate_render", fake_render)
    keys, errors, renders = agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph())
    assert len(keys) == 2 and len(errors) == 1
    assert sorted(r.provider for r in renders) == ["gemini", "pollinations"]
    assert keys[0].endswith(".png") and keys[1].endswith(".jpg")
    assert storage.load(keys[0]) == PNG


def test_moodboard_images_total_failure_returns_errors_not_raise(monkeypatch):
    monkeypatch.setattr(imagegen, "FREE_COOLDOWN_S", 0)  # no real waiting in the suite
    monkeypatch.setattr(
        imagegen, "generate_render", lambda p: (_ for _ in ()).throw(RuntimeError("all down"))
    )
    keys, errors, providers = agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph())
    assert keys == [] and providers == [] and len(errors) == 3


def test_a_render_is_draft_unless_a_contracted_provider_delivered_full_resolution():
    """The free tier caps at 768px whatever we request and no longer serves FLUX — its
    output indicates mood, it is not a client visual. The report captions it, so the
    flag has to be right."""
    assert not Render(PNG, ".png", "gemini", (1024, 1024)).is_draft
    assert Render(JPG, ".jpg", "pollinations", (1024, 1024)).is_draft   # not contracted
    assert Render(PNG, ".png", "gemini", (768, 768)).is_draft           # under-resolution
    assert Render(JPG, ".jpg", "pollinations", (768, 768)).is_draft


def test_classify_reports_the_real_pixel_size_and_rejects_a_truncated_image():
    """Magic bytes only prove the header. A header-valid truncated JPEG used to pass and
    then render as a grey box inside the client's PDF."""
    import pytest as _pytest

    assert imagegen._classify(PNG, "gemini").pixels == (1200, 1200)
    truncated = JPG[: len(JPG) // 3]
    assert truncated.startswith(b"\xff\xd8\xff") and len(truncated) > imagegen.MIN_IMAGE_BYTES
    with _pytest.raises(RuntimeError, match="undecodable"):
        imagegen._classify(truncated, "pollinations")


def test_render_batch_spaces_requests_so_the_free_provider_stops_refusing(monkeypatch):
    """Measured: the free provider 429s on back-to-back requests, which is why only one
    of three renders survived every run. Attempts after the first must be spaced."""
    monkeypatch.setattr(imagegen, "FREE_COOLDOWN_S", 5)
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))
    monkeypatch.setattr(
        imagegen, "generate_render", lambda p: Render(JPG, ".jpg", "pollinations", (768, 768))
    )
    keys, errors, renders = agents.generate_moodboard_images("S", ["oak"], "hotel", _graph())
    assert len(keys) == 3 and not errors
    assert waits == [5, 5], "two gaps for three sequential free-provider renders"


@pytest.mark.parametrize("provider", ["gemini", "flux"])
def test_render_batch_does_not_space_a_paid_provider(monkeypatch, provider):
    monkeypatch.setattr(imagegen, "FREE_COOLDOWN_S", 5)
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))
    monkeypatch.setattr(
        imagegen, "generate_render", lambda p: Render(PNG, ".png", provider, (1024, 1024))
    )
    keys, _errors, _renders = agents.generate_moodboard_images("S", ["oak"], "hotel", _graph())
    assert len(keys) == 3 and waits == []


def test_render_batch_stops_at_its_wall_clock_budget(monkeypatch):
    """A slow or drip-feeding provider must not stretch the pipeline without bound.
    The budget replaces the unrealistically short read timeout as that protection."""
    monkeypatch.setattr(imagegen, "FREE_COOLDOWN_S", 0)
    # monotonic returns 0 while starting and checking the first prompt, then jumps past
    # the 240s budget — one render lands, the rest are skipped with a visible reason.
    readings = [0.0, 0.0, 300.0]
    monkeypatch.setattr(
        "time.monotonic", lambda: readings.pop(0) if len(readings) > 1 else readings[0]
    )
    monkeypatch.setattr(
        imagegen, "generate_render", lambda p: Render(JPG, ".jpg", "pollinations", (768, 768))
    )
    keys, errors, _renders = agents.generate_moodboard_images("S", ["oak"], "hotel", _graph())
    assert len(keys) == 1, "the first render runs, then the budget stops the batch"
    assert any("budget" in e and "skipped" in e for e in errors)


def test_a_mid_batch_downgrade_to_the_free_tier_still_gets_spaced(monkeypatch):
    """Renders 1 and 2 can succeed on a paid provider, needing no cooldown, and then
    render 3 drops to the free tier. It used to arrive with no spacing and be refused —
    which is exactly what happened when FLUX ran out of credits partway through."""
    monkeypatch.setattr(imagegen, "FREE_COOLDOWN_S", 5)
    waits: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: waits.append(s))
    outcomes = iter([
        Render(JPG, ".jpg", "flux", (1024, 1024)),
        RuntimeError("gemini: 429; flux: 402 credits depleted; pollinations: 429"),
        Render(JPG, ".jpg", "pollinations", (768, 768)),
    ])

    def fake_render(prompt):
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(imagegen, "generate_render", fake_render)
    _keys, errors, _renders = agents.generate_moodboard_images("S", ["oak"], "hotel", _graph())
    # no wait before render 2 (only a paid render so far), one before render 3 (an error
    # means the free tier may already have been hit)
    assert waits == [5]
    # every provider's reason survives, so "402 credits depleted" reaches the operator
    assert "402 credits depleted" in errors[0] and "pollinations" in errors[0]


def test_resolution_summary_reports_a_range_for_a_mixed_batch():
    """A paid provider running out of credits partway leaves a mixed batch. Reporting
    only its best resolution reads as though every render came back full size."""
    def r(px):
        return Render(b"", ".jpg", "x", (px, px))

    assert imagegen.describe_resolution([r(768)]) == " at 768px"
    assert imagegen.describe_resolution([r(1024)]) == " at 1024px"
    assert imagegen.describe_resolution([r(1024), r(768)]) == " at 768–1024px"
    assert imagegen.describe_resolution([]) == ""
    assert imagegen.describe_resolution([Render(b"", ".jpg", "x", (0, 0))]) == ""


def test_flux_distinguishes_a_delivery_failure_from_a_generation_failure(monkeypatch):
    """The image is already paid for once the URL comes back, so a bad media host must
    not read as a model problem — that sends whoever reads the note to the wrong place."""
    class _Posted:
        headers = {"x-fal-billable-units": "1"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"images": [{"url": "https://media.example/never.jpg"}]}

    from app import settings

    monkeypatch.setattr(settings, "HUGGINGFACE_API_TOKEN", "hf_test")
    monkeypatch.setattr(imagegen.httpx, "post", lambda *a, **k: _Posted())
    monkeypatch.setattr(
        imagegen.httpx, "get",
        lambda *a, **k: (_ for _ in ()).throw(imagegen.httpx.ConnectError("host down")),
    )
    with pytest.raises(RuntimeError, match="delivery failed"):
        imagegen._flux("x")


def test_flux_reports_an_unreadable_body_instead_of_a_json_traceback(monkeypatch):
    class _Posted:
        headers: dict = {}

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("Expecting value: line 1 column 1")

    from app import settings

    monkeypatch.setattr(settings, "HUGGINGFACE_API_TOKEN", "hf_test")
    monkeypatch.setattr(imagegen.httpx, "post", lambda *a, **k: _Posted())
    with pytest.raises(RuntimeError, match="unreadable response"):
        imagegen._flux("x")


def test_the_flux_model_is_named_in_the_provider_label_and_priced_accordingly():
    """The receipt has to say WHICH model was billed: krea costs 8x schnell, so a label
    of just "flux" would make the money line unauditable."""
    from app import costs

    assert imagegen._flux_provider() == "flux-" + imagegen.FLUX_MODEL.rsplit("/", 1)[-1]
    assert imagegen._flux_provider() in costs.IMAGE_PRICES_USD, "the selected model must be priced"
    assert costs.IMAGE_PRICES_USD["flux-krea"] > costs.IMAGE_PRICES_USD["flux-schnell"]


def test_every_flux_model_counts_as_final_quality():
    """Draft-vs-final keys on the provider, and the label now carries a model suffix —
    a substring check that missed it would caption real renders as drafts."""
    for model in ("flux-krea", "flux-dev", "flux-schnell"):
        assert imagegen.is_final_quality(model)
        assert not Render(JPG, ".jpg", model, (1024, 1024)).is_draft
    assert not imagegen.is_final_quality("pollinations")
    assert imagegen.is_final_quality("gemini")


def test_image_cost_follows_the_units_the_provider_reports():
    """fal prices per megapixel, so a larger render bills more than one unit."""
    from app import costs

    token = costs.start_ledger()
    try:
        costs.record_image("moodboard", "flux-krea", units=1.0)
        assert costs.spent_usd() == pytest.approx(0.025)
        costs.record_image("moodboard", "flux-krea", units=4.0)   # a 2048x2048 render
        assert costs.spent_usd() == pytest.approx(0.125)
    finally:
        costs.stop_ledger(token)


# Adversarial review of the deployment (2026-08-07): the billable-units header is a
# trust boundary, and mishandling it threw away renders we had already paid for.

@pytest.mark.parametrize("raw,expected", [
    ("1", 1.0),
    ("2.5", 2.5),
    ("abc", 1.0),        # a garbage value must not lose the charge
    ("1,5", 1.0),        # locale-formatted upstream — raised out of the provider before
    (None, 1.0),         # header absent entirely
    ("", 1.0),
    ("inf", 1.0),        # tripped the analysis budget and aborted a paying client's run
    ("1e400", 1.0),      # overflows to inf
    ("nan", 1.0),
    ("-3", 1.0),         # a real charge must never be recorded as free
    ("0", 1.0),
])
def test_billable_units_never_loses_or_explodes_a_charge(raw, expected):
    headers = {} if raw is None else {"x-fal-billable-units": raw}
    assert imagegen._billable_units(headers) == pytest.approx(expected)


def test_a_garbage_units_header_still_bills_and_still_returns_a_final_render(monkeypatch):
    """The whole failure: fal generated and delivered a 1024px image, we paid, and the
    client got a draft with a $0.00 receipt because the header would not parse."""
    from app import costs, settings

    class _Posted:
        headers = {"x-fal-billable-units": "1,5"}

        def raise_for_status(self):
            return None

        def json(self):
            return {"images": [{"url": "https://media.example/x.jpg"}]}

    class _Fetched:
        content = JPG

        def raise_for_status(self):
            return None

    monkeypatch.setattr(settings, "HUGGINGFACE_API_TOKEN", "hf_test")
    monkeypatch.setattr(imagegen.httpx, "post", lambda *a, **k: _Posted())
    monkeypatch.setattr(imagegen.httpx, "get", lambda *a, **k: _Fetched())

    token = costs.start_ledger()
    try:
        render = imagegen._flux("a lobby")
        assert not render.is_draft, "a paid 1024px render must not be captioned draft"
        assert costs.spent_usd() > 0, "a paid render must not be recorded as free"
    finally:
        costs.stop_ledger(token)
