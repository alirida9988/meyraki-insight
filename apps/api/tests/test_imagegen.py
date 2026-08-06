"""Gemini image generation — response parsing and failure policy (offline)."""

import base64

import pytest

from app import agents
from app.agents import WireZoneGraph, repair_zone_graph
from app.imagegen import extract_image
from tests.test_agents import _wz

PNG = b"\x89PNG\r\n\x1a\nfake"
B64 = base64.b64encode(PNG).decode()


def test_extract_image_camel_and_snake_case():
    camel = {"candidates": [{"content": {"parts": [{"inlineData": {"data": B64}}]}}]}
    snake = {"candidates": [{"content": {"parts": [{"text": "x"}, {"inline_data": {"data": B64}}]}}]}
    assert extract_image(camel) == PNG
    assert extract_image(snake) == PNG


def test_extract_image_no_image_raises_with_reason():
    data = {"candidates": [{"content": {"parts": [{"text": "refused"}]}, "finishReason": "SAFETY"}]}
    with pytest.raises(RuntimeError, match="SAFETY"):
        extract_image(data)


def _graph():
    return repair_zone_graph(
        WireZoneGraph(zones=[_wz("lobby")], adjacency=[], entrances=["lobby"])
    )


def test_moodboard_images_partial_success_kept(monkeypatch, tmp_path):
    from app import imagegen, settings, storage

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    calls = iter([PNG, RuntimeError("boom"), PNG])

    def fake_render(prompt):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(imagegen, "generate_render", fake_render)
    keys, errors = agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph())
    assert len(keys) == 2
    assert len(errors) == 1 and "boom" in errors[0]
    assert all(storage.load(k) == PNG for k in keys)


def test_moodboard_images_total_failure_returns_errors_not_raise(monkeypatch):
    from app import imagegen, settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        imagegen, "generate_render", lambda p: (_ for _ in ()).throw(RuntimeError("429 quota"))
    )
    keys, errors = agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph())
    assert keys == []
    assert len(errors) == 3  # one per attempted render — caller surfaces them


def test_moodboard_images_skipped_without_key(monkeypatch):
    from app import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    assert agents.generate_moodboard_images("Style", ["oak"], "hotel", _graph()) == ([], [])
