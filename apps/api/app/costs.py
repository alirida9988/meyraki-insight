"""Per-analysis cost ledger and the ceiling that stops a runaway run.

`docs/03-TECH-STACK-DECISIONS.md` promises "Cost guard: per-analysis budget enforced by
the Routing Agent" and `ExecutionPlan` has carried `total_budget_usd` and a per-step
`max_usd` since M1. Nothing enforced either, and nothing recorded what a run actually
spent — the same shape of fiction as the hardcoded `solver_feasible = False`. Now that a
single analysis calls Haiku, Sonnet, Opus and a paid image provider, an unbounded retry
loop spends real money with no ceiling and no receipt.

Prices are the published list rates from `docs/03-TECH-STACK-DECISIONS.md`. Sonnet 5 has
introductory pricing ($2/$10) running to 2026-08-31, which this table deliberately does
NOT use: a ceiling must never under-estimate spend, so the standard rate is the safe
direction and actual bills during the intro period come in under what we report. That
assumption travels with the number wherever it is shown.

All ledger state lives in ONE ContextVar holding one dict, so `start_ledger` /
`stop_ledger` nest correctly on a single token. Three separate vars looked tidier and
silently clobbered an outer analysis's carried spend. It is a ContextVar rather than a
module global because analyses run concurrently in FastAPI's background threadpool — a
shared ledger would bill one client's tokens to another's budget.
"""

import contextvars
import os

# USD per 1,000,000 tokens, (input, output).
TOKEN_PRICES_USD: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
}

# USD per generated image.
IMAGE_PRICES_USD: dict[str, float] = {
    "gemini": 0.039,
    "flux": 0.003,
    "pollinations": 0.0,
}

# Default ceiling per analysis, and the hard cap: a plan may ask for less, never more.
DEFAULT_BUDGET_USD = float(os.environ.get("MEYRAKI_ANALYSIS_BUDGET_USD", "1.50"))

# Per-step ceilings. `StepBudget.max_usd` was declared at a flat $0.20 and enforced
# nowhere — 5x below what one layout call really costs. These come from a measured
# Cleo-class run (intake $0.0027 / zones $0.0288 / layout $0.0752 / moodboard $0.0121 /
# report $0.0299) with headroom for one retry, and they are now actually checked.
STEP_MAX_USD: dict[str, float] = {
    "intake": 0.10,
    "routing": 0.02,
    "zones": 0.25,
    "flow": 0.02,
    "layout": 0.45,
    "moodboard": 0.25,
    "business": 0.02,
    "report": 0.25,
    "qa": 0.10,
}
DEFAULT_STEP_MAX_USD = 0.25

UNKNOWN_MODEL_USD_PER_MTOK = (15.00, 75.00)
"""An unrecognised model is priced at the most expensive tier we know rather than free.
A new model id must not silently cost nothing and slip past the ceiling."""

USAGE_UNAVAILABLE = "usage-unavailable"
"""Marker entry when a provider returns no token counts — visible in the receipt rather
than a silent $0 (the project rule is that degradation is always visible)."""


def _blank() -> dict:
    return {"entries": [], "prior": 0.0, "flushed": 0, "budget": DEFAULT_BUDGET_USD}


_STATE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "meyraki_cost_state", default=None
)


class BudgetExceeded(RuntimeError):
    """Raised instead of starting work that would run past a ceiling."""


def start_ledger(prior_usd: float = 0.0) -> contextvars.Token:
    """Begin accounting for one analysis. Returns a token for `stop_ledger`.

    `prior_usd` carries spend from earlier runs of the same analysis so a resume
    continues against the same ceiling rather than restarting it.
    """
    state = _blank()
    state["prior"] = max(0.0, prior_usd)
    return _STATE.set(state)


def stop_ledger(token: contextvars.Token) -> None:
    _STATE.reset(token)


def _state() -> dict | None:
    return _STATE.get()


def set_budget(budget_usd: float) -> None:
    """The ceiling in force for this analysis, so a check deep inside a step (the layout
    retry) uses the same number the runner does instead of the module default."""
    state = _state()
    if state is not None:
        state["budget"] = budget_usd


def current_budget_usd() -> float:
    state = _state()
    return state["budget"] if state else DEFAULT_BUDGET_USD


def token_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = TOKEN_PRICES_USD.get(model, UNKNOWN_MODEL_USD_PER_MTOK)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def _append(step: str, model: str, input_tokens: int, output_tokens: int, usd: float) -> float:
    state = _state()
    if state is not None:
        state["entries"].append({
            "step": step,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "usd": usd,
        })
    return usd


def record_tokens(step: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """Book a model call. No-op outside an analysis (scripts, tests, the golden set).

    Counts are clamped at zero: a vendor response is a trust boundary, and a negative
    count once produced a NEGATIVE running total, which disables the ceiling outright.
    """
    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    return _append(step, model, input_tokens, output_tokens,
                   token_cost_usd(model, input_tokens, output_tokens))


def record_usage_unavailable(step: str, model: str) -> None:
    """A provider returned no usage. Leaves a visible marker instead of a silent $0."""
    _append(step, f"{model} ({USAGE_UNAVAILABLE})", 0, 0, 0.0)


def record_image(step: str, provider: str) -> float:
    """Book a generated image. Priced at the dearest known provider when unrecognised,
    for the same reason unknown models are: a new provider must not cost nothing."""
    fallback = max(IMAGE_PRICES_USD.values())
    return _append(step, f"image:{provider}", 0, 0, IMAGE_PRICES_USD.get(provider, fallback))


def spent_usd() -> float:
    """Everything this analysis has spent, including earlier runs of it."""
    state = _state()
    if state is None:
        return 0.0
    return round(state["prior"] + sum(e["usd"] for e in state["entries"]), 6)


def spent_on_step_usd(step: str) -> float:
    state = _state()
    if state is None:
        return 0.0
    return round(sum(e["usd"] for e in state["entries"] if e["step"] == step), 6)


def entries() -> list[dict]:
    state = _state()
    return list(state["entries"]) if state else []


def unflushed() -> list[dict]:
    """Entries not yet written to the database. Does NOT advance the cursor — the caller
    calls `mark_flushed` only after its commit succeeds, because advancing first meant a
    failed commit (SQLite `database is locked`, the concurrent-run case this feature
    targets) dropped those entries from the receipt permanently."""
    state = _state()
    return state["entries"][state["flushed"]:] if state else []


def mark_flushed(count: int) -> None:
    """Confirm `count` entries reached the database."""
    state = _state()
    if state is not None:
        state["flushed"] = min(len(state["entries"]), state["flushed"] + max(0, count))


def check_budget(step: str, budget_usd: float) -> None:
    """Raise before starting `step` if the analysis, or that step, has spent its ceiling.

    Checked before the work rather than after, because the point is to not start what
    cannot be paid for — catching it afterwards is a receipt, not a guard.
    """
    spent = spent_usd()
    if spent >= budget_usd:
        raise BudgetExceeded(
            f"analysis budget of ${budget_usd:.2f} reached (${spent:.4f} spent) before "
            f"step '{step}' — no further model calls were made"
        )
    step_cap = STEP_MAX_USD.get(step, DEFAULT_STEP_MAX_USD)
    step_spent = spent_on_step_usd(step)
    if step_spent >= step_cap:
        raise BudgetExceeded(
            f"step '{step}' reached its ${step_cap:.2f} ceiling (${step_spent:.4f} spent) "
            f"— a single step cannot consume the whole analysis budget"
        )


def summary() -> str:
    """One line for the pipeline register. The parts must add up to the total, or it is
    a money line that contradicts itself in front of the founder."""
    state = _state()
    if state is None:
        return "no billable model calls recorded"
    ledger, prior = state["entries"], state["prior"]
    if not ledger and not prior:
        return "no billable model calls recorded"
    by_model: dict[str, float] = {}
    if prior:
        by_model["earlier runs"] = prior
    for entry in ledger:
        by_model[entry["model"]] = by_model.get(entry["model"], 0.0) + entry["usd"]
    parts = ", ".join(f"{m} ${v:.4f}" for m, v in sorted(by_model.items()))
    return f"${spent_usd():.4f} total ({parts})"
