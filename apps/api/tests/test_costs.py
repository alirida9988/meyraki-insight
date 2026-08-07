"""Cost ledger and the per-analysis ceiling.

docs/03-TECH-STACK-DECISIONS.md promised "Cost guard: per-analysis budget enforced by
the Routing Agent" and ExecutionPlan has carried total_budget_usd since M1. Neither was
enforced and nothing recorded actual spend, so these tests exist to keep that honest.
"""

import threading

import pytest

from app import costs


def test_prices_match_the_documented_rate_card():
    """Priced from docs/03. A drifting table silently misreports every analysis."""
    assert costs.TOKEN_PRICES_USD["claude-haiku-4-5"] == (1.00, 5.00)
    assert costs.TOKEN_PRICES_USD["claude-sonnet-5"] == (3.00, 15.00)
    assert costs.TOKEN_PRICES_USD["claude-opus-5"] == (5.00, 25.00)
    # every model the agents actually call has a price
    from app import agents

    for model in (agents.INTAKE_MODEL, agents.ZONES_MODEL, agents.LAYOUT_MODEL,
                  agents.MOODBOARD_MODEL, agents.REPORT_MODEL):
        assert model in costs.TOKEN_PRICES_USD, f"{model} is called but has no price"


def test_an_unknown_model_is_priced_at_the_top_tier_not_free():
    """A new model id must not silently cost nothing and slip past the ceiling."""
    known = costs.token_cost_usd("claude-opus-5", 1_000_000, 0)
    unknown = costs.token_cost_usd("claude-something-new", 1_000_000, 0)
    assert unknown >= known > 0


def test_token_cost_arithmetic():
    # 1M input + 1M output on Sonnet = $3 + $15
    assert costs.token_cost_usd("claude-sonnet-5", 1_000_000, 1_000_000) == pytest.approx(18.0)
    assert costs.token_cost_usd("claude-haiku-4-5", 500_000, 0) == pytest.approx(0.5)


def test_recording_outside_an_analysis_is_a_no_op_not_a_crash():
    """The golden set and one-off scripts call agents with no ledger open."""
    assert costs.record_tokens("zones", "claude-sonnet-5", 1000, 1000) > 0
    assert costs.spent_usd() == 0.0
    assert costs.entries() == []


def test_ledger_accumulates_tokens_and_images():
    token = costs.start_ledger()
    try:
        costs.record_tokens("zones", "claude-sonnet-5", 1_000_000, 0)   # $3
        costs.record_image("moodboard", "flux-krea")                     # $0.025
        costs.record_image("moodboard", "pollinations")                  # free
        assert costs.spent_usd() == pytest.approx(3.025)
        assert len(costs.entries()) == 3
        assert "claude-sonnet-5" in costs.summary() and "image:flux-krea" in costs.summary()
    finally:
        costs.stop_ledger(token)


def test_the_ceiling_stops_the_next_step_rather_than_reporting_afterwards():
    token = costs.start_ledger()
    try:
        costs.check_budget("zones", 1.50)          # nothing spent yet — fine
        costs.record_tokens("zones", "claude-opus-5", 1_000_000, 0)  # $5
        with pytest.raises(costs.BudgetExceeded, match=r"budget of \$1.50 reached"):
            costs.check_budget("layout", 1.50)
    finally:
        costs.stop_ledger(token)


def test_concurrent_analyses_do_not_share_a_ledger():
    """Analyses run in FastAPI's background threadpool. A module-global ledger would
    bill one client's tokens to another's budget — and trip the wrong ceiling."""
    seen: dict[str, float] = {}

    def analysis(name: str, tokens: int) -> None:
        token = costs.start_ledger()
        try:
            costs.record_tokens("zones", "claude-sonnet-5", tokens, 0)
            seen[name] = costs.spent_usd()
        finally:
            costs.stop_ledger(token)

    threads = [
        threading.Thread(target=analysis, args=("a", 1_000_000)),
        threading.Thread(target=analysis, args=("b", 2_000_000)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen["a"] == pytest.approx(3.0)
    assert seen["b"] == pytest.approx(6.0), "b must not see a's spend, or vice versa"


def test_execution_plan_names_the_models_it_will_bill():
    """The plan used to claim model="stub" for every step, including the ones that
    spend real money on Opus."""
    from app.pipeline import STEP_MODELS

    assert STEP_MODELS["layout"] == "claude-opus-5"
    assert STEP_MODELS["zones"] == "claude-sonnet-5"
    for model in STEP_MODELS.values():
        assert model in costs.TOKEN_PRICES_USD


# ---------------------------------------------------------------- end to end

import os  # noqa: E402

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_meyraki.db")
os.environ.setdefault("UPLOAD_DIR", "var/test_uploads")
os.environ.setdefault("MEYRAKI_USE_AGENTS", "off")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Analysis, CostEntry, Event  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/auth/register", json={
            "email": f"costorg-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "CostOrg",
        })
        yield c


@pytest.fixture(scope="module")
def project_id(client):
    return client.post("/projects", json={"name": "Costs", "space_type": "cafe"}).json()["id"]


def _start_analysis(client, project_id) -> str:
    plan = client.post(
        f"/projects/{project_id}/uploads",
        params={"kind": "floorplan"},
        files={"file": ("p.png", PNG, "image/png")},
    ).json()
    return client.post(
        f"/projects/{project_id}/analyses",
        json={"floorplan_upload_id": plan["id"], "objectives": ["guest_flow"]},
    ).json()["id"]


def test_a_runaway_analysis_is_stopped_by_the_ceiling_and_leaves_a_receipt(
    client, project_id, monkeypatch
):
    """The guard has to bite in the real runner, not just in isolation: remaining steps
    are skipped rather than failed, the reason names the ceiling, and the spend that did
    happen is persisted so a stopped run still has its receipt."""
    from app import pipeline

    real_zones = pipeline.STEPS["zones"]

    def expensive_zones(ctx):
        costs.record_tokens("zones", "claude-opus-5", 2_000_000, 0)  # $10, over any ceiling
        return real_zones(ctx)

    monkeypatch.setitem(pipeline.STEPS, "zones", expensive_zones)
    aid = _start_analysis(client, project_id)

    for _ in range(60):
        detail = client.get(f"/analyses/{aid}").json()
        if detail["status"] in ("done", "failed", "rejected"):
            break
        import time as _time

        _time.sleep(0.1)

    assert detail["status"] == "failed"
    assert "budget" in (detail.get("error") or "").lower()

    with SessionLocal() as s:
        steps = {st.name: st.status for st in s.get(Analysis, aid).steps}
        entries = s.query(CostEntry).filter(CostEntry.analysis_id == aid).all()
        notes = [e.message for e in s.query(Event).filter(Event.analysis_id == aid).all()]

    assert steps["zones"] == "done", "the step that ran keeps its output"
    assert steps["layout"] == "skipped", "unpaid work is skipped, not failed"
    assert entries and entries[0].model == "claude-opus-5"
    assert entries[0].usd == pytest.approx(10.0)
    assert any("Budget ceiling hit" in n for n in notes)


def test_a_normal_analysis_records_its_spend_and_finishes(client, project_id):
    """With agents off nothing is billable, so the run completes and the register still
    reports the spend — a receipt of $0 is information, not an error."""
    aid = _start_analysis(client, project_id)
    for _ in range(60):
        detail = client.get(f"/analyses/{aid}").json()
        if detail["status"] in ("done", "failed", "rejected"):
            break
        import time as _time

        _time.sleep(0.1)

    assert detail["status"] == "done"
    with SessionLocal() as s:
        notes = [e.message for e in s.query(Event).filter(Event.analysis_id == aid).all()]
    assert any("Model spend:" in n for n in notes)


def test_the_analysis_endpoint_reports_what_the_run_cost(client, project_id):
    """An enforced budget with no receipt is only half a fix — the spend has to be
    visible where someone would look for it."""
    aid = _start_analysis(client, project_id)
    for _ in range(60):
        detail = client.get(f"/analyses/{aid}").json()
        if detail["status"] in ("done", "failed", "rejected"):
            break
        import time as _time

        _time.sleep(0.1)

    assert "cost" in detail and "usd" in detail["cost"]
    assert isinstance(detail["cost"]["by_step"], list)


def test_cost_is_org_scoped_like_everything_else(client, project_id):
    """Another studio must not be able to read what this one spent."""
    aid = _start_analysis(client, project_id)
    other = TestClient(app)
    with other as o:
        o.post("/auth/register", json={
            "email": f"rival-{os.getpid()}@test.dev",
            "password": "test-password-1",
            "org_name": "Rival",
        })
        assert o.get(f"/analyses/{aid}").status_code == 404


# Resume: found by testing before the reviewer got there (2026-08-07)

def test_a_resumed_run_continues_against_the_same_ceiling():
    """Without carrying prior spend, an analysis resumed N times gets N x the budget —
    which defeats the guard entirely."""
    first = costs.start_ledger()
    costs.record_tokens("layout", "claude-opus-5", 200_000, 0)   # $1.00
    spent_first = costs.spent_usd()
    costs.stop_ledger(first)
    assert spent_first == pytest.approx(1.0)

    resumed = costs.start_ledger(prior_usd=spent_first)
    try:
        assert costs.spent_usd() == pytest.approx(1.0), "the resume starts where we left off"
        costs.record_tokens("report", "claude-opus-5", 120_000, 0)  # +$0.60 -> $1.60
        with pytest.raises(costs.BudgetExceeded):
            costs.check_budget("qa", 1.50)
    finally:
        costs.stop_ledger(resumed)


def test_a_resumed_run_still_records_its_own_receipt():
    """Persistence used to be driven by the DB row count, so on a resume the prior run's
    rows made the offset larger than the new in-memory ledger and every new entry was
    silently dropped."""
    token = costs.start_ledger(prior_usd=0.42)   # as if 5 rows already exist in the DB
    try:
        costs.record_tokens("layout", "claude-opus-5", 1000, 1000)
        costs.record_tokens("report", "claude-sonnet-5", 1000, 1000)
        pending = costs.unflushed()
        assert len(pending) == 2, "new spend must be written regardless of DB history"
        costs.mark_flushed(len(pending))
        assert costs.unflushed() == [], "a confirmed flush must not duplicate rows"
        costs.record_image("moodboard", "flux")
        assert len(costs.unflushed()) == 1, "later entries still flush"
    finally:
        costs.stop_ledger(token)


def test_stopping_the_ledger_clears_carried_spend_for_the_next_analysis():
    """A leaked prior balance would charge the next analysis on this worker thread."""
    token = costs.start_ledger(prior_usd=5.0)
    costs.stop_ledger(token)
    assert costs.spent_usd() == 0.0
    assert costs.unflushed() == []


# Adversarial review of the cost guard (2026-08-07) — one test per confirmed finding.

def test_a_failed_commit_does_not_erase_entries_from_the_receipt():
    """The cursor used to advance before the commit, so one `database is locked` — the
    concurrent-run case this feature targets — dropped those entries permanently."""
    token = costs.start_ledger()
    try:
        costs.record_tokens("layout", "claude-opus-5", 100_000, 0)
        costs.record_tokens("report", "claude-sonnet-5", 100_000, 0)
        pending = costs.unflushed()
        assert len(pending) == 2
        # commit fails -> mark_flushed is never called
        assert len(costs.unflushed()) == 2, "entries stay pending until the write lands"
        costs.mark_flushed(len(pending))
        assert costs.unflushed() == []
    finally:
        costs.stop_ledger(token)


def test_negative_usage_from_a_provider_cannot_disable_the_ceiling():
    """A vendor response is a trust boundary: a negative count once produced a NEGATIVE
    running total, which switches the guard off for the rest of the run."""
    token = costs.start_ledger()
    try:
        costs.record_tokens("zones", "claude-opus-5", -5_000_000, -5_000_000)
        assert costs.spent_usd() == 0.0
        costs.record_tokens("zones", "claude-opus-5", 1_000_000, 0)
        assert costs.spent_usd() == pytest.approx(5.0)
    finally:
        costs.stop_ledger(token)


def test_an_unknown_image_provider_is_not_free():
    """Mirrors the unknown-model rule: a new provider must not silently cost nothing."""
    token = costs.start_ledger()
    try:
        costs.record_image("moodboard", "some-new-provider")
        assert costs.spent_usd() == pytest.approx(max(costs.IMAGE_PRICES_USD.values()))
    finally:
        costs.stop_ledger(token)


def test_a_nested_ledger_restores_the_outer_analysis_spend():
    """Three separate ContextVars looked tidier and clobbered the outer carried spend."""
    outer = costs.start_ledger(prior_usd=6.0)
    try:
        inner = costs.start_ledger(prior_usd=0.0)
        costs.record_tokens("zones", "claude-sonnet-5", 1000, 0)
        costs.stop_ledger(inner)
        assert costs.spent_usd() == pytest.approx(6.0), "the outer balance survives"
    finally:
        costs.stop_ledger(outer)


def test_one_step_cannot_consume_the_whole_analysis_budget():
    """The ceiling is checked between steps, but layout makes two Opus calls inside one
    step — enough to overshoot the analysis budget without ever crossing a boundary."""
    token = costs.start_ledger()
    try:
        costs.record_tokens("layout", "claude-opus-5", 50_000, 0)   # $0.25, under the $0.45 cap
        costs.check_budget("layout", 1.50)
        costs.record_tokens("layout", "claude-opus-5", 0, 16_000)    # +$0.40 -> $0.65, over it
        with pytest.raises(costs.BudgetExceeded, match="step 'layout' reached"):
            costs.check_budget("layout", 1.50)
        # a different step is unaffected by layout's spend against ITS cap
        costs.check_budget("report", 1.50)
    finally:
        costs.stop_ledger(token)


def test_summary_parts_add_up_to_the_total_when_spend_is_carried():
    """A founder-facing money line that contradicts itself is worse than no line."""
    token = costs.start_ledger(prior_usd=1.20)
    try:
        costs.record_tokens("report", "claude-sonnet-5", 100_000, 0)  # $0.30
        line = costs.summary()
        assert "$1.5000 total" in line
        assert "earlier runs $1.2000" in line and "claude-sonnet-5 $0.3000" in line
    finally:
        costs.stop_ledger(token)


def test_the_execution_plan_carries_the_configured_ceiling_not_the_contract_default():
    """The plan never set total_budget_usd, so the contract default silently overrode
    MEYRAKI_ANALYSIS_BUDGET_USD for every step that spends money."""
    from app import pipeline

    class _Analysis:
        footfall_upload_id = None
        objectives = ["guest_flow"]

    class _Ctx:
        analysis = _Analysis()
        outputs: dict = {}

    plan = pipeline.step_routing(_Ctx())
    assert plan.total_budget_usd == costs.DEFAULT_BUDGET_USD
    by_step = {b.step: b for b in plan.budgets}
    assert by_step["layout"].max_usd == costs.STEP_MAX_USD["layout"]
    assert by_step["layout"].model == "claude-opus-5"


def test_a_stored_plan_can_never_raise_the_operators_ceiling():
    from app import pipeline

    class _Ctx:
        outputs = {"routing": {"total_budget_usd": 99.0}}

    assert pipeline._budget_for(_Ctx()) == costs.DEFAULT_BUDGET_USD

    class _Modest:
        outputs = {"routing": {"total_budget_usd": 0.10}}

    assert pipeline._budget_for(_Modest()) == 0.10


@pytest.mark.parametrize("outcome", ["refusal", "max_tokens", "unparseable", "validation_error"])
def test_a_billed_call_is_recorded_even_when_its_output_is_rejected(monkeypatch, outcome):
    """The three raises after a successful call booked $0.00, so exactly the runaway the
    ceiling exists to stop was invisible to it — and a resumed run inherited a $0 receipt
    and a fresh budget."""
    import pydantic

    from app import agents, settings

    class _Usage:
        input_tokens = 100_000
        output_tokens = 16_000

    class _Response:
        usage = _Usage()
        stop_reason = "end_turn"
        parsed_output = object()

    response = _Response()
    if outcome == "refusal":
        response.stop_reason = "refusal"
    elif outcome == "max_tokens":
        response.stop_reason = "max_tokens"
    elif outcome == "unparseable":
        response.parsed_output = None

    class _Messages:
        def parse(self, **kwargs):
            if outcome == "validation_error":
                class _T(pydantic.BaseModel):
                    x: int

                _T.model_validate_json('{"x": ')
            return response

    monkeypatch.setattr(settings, "agents_enabled", lambda: True)
    monkeypatch.setattr(agents, "client", lambda: type("C", (), {"messages": _Messages()})())

    token = costs.start_ledger()
    try:
        with pytest.raises(Exception):
            agents._parse("claude-opus-5", 16_000, [], agents.WireZoneGraph, step="layout")
        assert costs.spent_usd() > 0, f"{outcome} was billed but recorded $0"
    finally:
        costs.stop_ledger(token)


def test_a_provider_without_usage_leaves_a_visible_marker_not_a_silent_zero():
    from app import agents, settings

    class _Response:
        usage = None
        stop_reason = "end_turn"
        parsed_output = {"ok": True}

    class _Messages:
        def parse(self, **kwargs):
            return _Response()

    import pytest as _pytest

    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(settings, "agents_enabled", lambda: True)
    monkeypatch.setattr(agents, "client", lambda: type("C", (), {"messages": _Messages()})())
    token = costs.start_ledger()
    try:
        agents._parse("claude-sonnet-5", 100, [], agents.WireZoneGraph, step="zones")
        assert any(costs.USAGE_UNAVAILABLE in e["model"] for e in costs.entries())
    finally:
        costs.stop_ledger(token)
        monkeypatch.undo()


def test_a_check_inside_a_step_uses_the_analysis_ceiling_not_the_module_default():
    """The layout retry gate checked DEFAULT_BUDGET_USD, so a plan configured lower was
    not honoured inside the step that spends the most."""
    token = costs.start_ledger()
    try:
        costs.set_budget(0.10)
        assert costs.current_budget_usd() == 0.10
        costs.record_tokens("layout", "claude-opus-5", 30_000, 0)  # $0.15 > $0.10
        with pytest.raises(costs.BudgetExceeded, match=r"budget of \$0.10"):
            costs.check_budget("layout", costs.current_budget_usd())
    finally:
        costs.stop_ledger(token)
