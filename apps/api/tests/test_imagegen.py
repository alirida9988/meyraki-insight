"""Render provider chain — parsing, classification, fallback, failure policy."""

import base64

import pytest

from app import agents, imagegen
from app.agents import WireZoneGraph, repair_zone_graph
from app.imagegen import Render, _classify, extract_image, generate_render, media_type
from tests.test_agents import _wz

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20_000
JPG = b"\xff\xd8\xff\xe0" + b"0" * 20_000
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
    outcomes = iter([Render(PNG, ".png", "gemini"), RuntimeError("boom"), Render(JPG, ".jpg", "pollinations")])

    def fake_render(prompt):
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(imagegen, "generate_render", fake_render)
    keys, errors, providers = agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph())
    assert len(keys) == 2 and len(errors) == 1
    assert sorted(providers) == ["gemini", "pollinations"]
    assert keys[0].endswith(".png") and keys[1].endswith(".jpg")
    assert storage.load(keys[0]) == PNG


def test_moodboard_images_total_failure_returns_errors_not_raise(monkeypatch):
    monkeypatch.setattr(
        imagegen, "generate_render", lambda p: (_ for _ in ()).throw(RuntimeError("all down"))
    )
    keys, errors, providers = agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph())
    assert keys == [] and providers == [] and len(errors) == 3
