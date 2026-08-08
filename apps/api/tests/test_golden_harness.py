"""The golden-set harness itself — a benchmark that lies is worse than no benchmark.

Offline: the real scorer is stubbed out, so no plan is read and no model is called.
"""

import os

import anthropic

from tests import golden_set

HARD, SOFT = golden_set.HARD, golden_set.SOFT


def test_importing_the_harness_never_turns_agents_on():
    """golden_set.py sets MEYRAKI_USE_AGENTS=auto for standalone runs; importing it
    from the offline suite must not switch live model calls on for everything else."""
    assert os.environ["MEYRAKI_USE_AGENTS"] == "off"


def test_a_crashing_plan_fails_the_run_even_at_a_passing_score(monkeypatch, capsys):
    """A crash yields one failed check instead of the seven-plus it should have, which
    shrinks the denominator and flatters the average to ~94% — above the threshold."""
    def scorer(name, spec):
        if name == "cleo_hotel.png":
            raise RuntimeError("vision API 400")
        return [(f"{name}: fine", True, "", SOFT)], {"plan": name}

    monkeypatch.setattr(golden_set, "score_plan", scorer)
    assert golden_set.main() == 1
    out = capsys.readouterr().out
    assert "FATAL" in out and "cleo_hotel.png" in out


def test_one_hard_failure_fails_the_run_at_any_score(monkeypatch, capsys):
    """Accepting a bar chart as a floorplan was worth 8% of the score and passed.
    No percentage may excuse a hard check."""
    def scorer(name, spec):
        checks = [(f"{name}: soft {i}", True, "", SOFT) for i in range(20)]
        if name == "sales_chart.png":
            checks.append((f"{name}: intake quality == not_a_floorplan", False, "got ok", HARD))
        return checks, {"plan": name}

    monkeypatch.setattr(golden_set, "score_plan", scorer)
    assert golden_set.main() == 1
    out = capsys.readouterr().out
    assert "hard check(s) failed" in out
    # The score stays comfortably above the threshold and the run still fails — that is
    # the whole point. Derived, not hardcoded: the fixture set grows.
    import re

    score = float(re.search(r"= (\d+\.\d)%", out).group(1))
    assert score > golden_set.THRESHOLD * 100, f"score {score}% should have passed on its own"


def test_soft_failures_are_governed_by_the_threshold(monkeypatch):
    def scorer(name, spec):
        # 1 in 4 soft checks fails per plan → 75%, below the 85% threshold
        return [(f"{name}: s{i}", i != 0, "", SOFT) for i in range(4)], {"plan": name}

    monkeypatch.setattr(golden_set, "score_plan", scorer)
    assert golden_set.main() == 1


def test_a_clean_run_passes(monkeypatch):
    monkeypatch.setattr(
        golden_set, "score_plan", lambda name, spec: ([(name, True, "", HARD)], {"plan": name})
    )
    assert golden_set.main() == 0


def test_a_throttled_api_is_inconclusive_not_a_model_regression(monkeypatch, capsys):
    """On a paid benchmark you cannot cheaply re-run, a 429 must not read as drift."""
    def scorer(name, spec):
        raise golden_set.Inconclusive("intake: RateLimitError")

    monkeypatch.setattr(golden_set, "score_plan", scorer)
    assert golden_set.main() == 2
    assert "INCONCLUSIVE" in capsys.readouterr().out


def test_api_errors_are_classified_as_inconclusive_at_the_source(monkeypatch):
    """score_plan must translate transport failures itself, so main() cannot mistake
    them for content failures."""
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)

    def throttled(*_args, **_kwargs):
        raise anthropic.RateLimitError("slow down", response=response, body=None)

    monkeypatch.setattr(golden_set.agents, "run_intake", throttled)
    monkeypatch.setattr(
        golden_set, "GOLDEN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
    )
    try:
        golden_set.score_plan("cleo_hotel.png", {"quality": "ok"})
    except golden_set.Inconclusive as exc:
        assert "RateLimitError" in str(exc)
    else:
        raise AssertionError("a 429 must raise Inconclusive")
