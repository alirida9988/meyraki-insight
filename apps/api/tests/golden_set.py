"""Golden-set benchmark — catches model and prompt drift on plan understanding.

Labelled plans in tests/golden/ with the properties a correct reading must have.
Runs the real Intake Agent and Zone Analyst, scores every check, and fails when a
HARD check fails or the overall score falls below THRESHOLD. Run it before changing
a prompt or a model id, and after: a silent accuracy regression is the failure mode
this exists for.

    .venv/bin/python tests/golden_set.py            # score and report
    .venv/bin/python tests/golden_set.py --save     # also write artifacts/

Exit codes: 0 pass · 1 fail · 2 inconclusive (the API was unreachable, rate limited,
or overloaded — that is not a model regression and must not be reported as one).

Opt-in by design: it spends model credits, so it is never part of the offline pytest
suite (CI guards that, and agents._parse refuses to fire when agents are disabled).
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("MEYRAKI_USE_AGENTS", "auto")

import anthropic  # noqa: E402

from app import agents, imaging  # noqa: E402
from meyraki_contracts import FootfallStatus, PlanQuality  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
THRESHOLD = 0.85

# A check is HARD when no percentage should ever excuse it. Accepting a bar chart as a
# floorplan is the product's worst failure mode — garbage in, invented zones, invoiced
# report — and as one soft check among 24 it was worth 8% and passed comfortably.
HARD = True
SOFT = False

Check = tuple[str, bool, str, bool]  # (label, ok, note, hard)


class Inconclusive(RuntimeError):
    """The API was unreachable or throttled. Not evidence about the model."""


def score_plan(name: str, spec: dict) -> tuple[list[Check], dict]:
    """Returns (checks, detail) for one labelled plan."""
    with open(os.path.join(GOLDEN, name), "rb") as handle:
        data = handle.read()
    checks: list[Check] = []
    detail: dict = {"plan": name, "note": spec.get("note", "")}

    try:
        manifest = agents.run_intake(data, FootfallStatus.NONE)
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        raise Inconclusive(f"intake: {type(exc).__name__}") from exc

    detail["plan_quality"] = manifest.plan_quality.value
    checks.append((
        f"{name}: intake quality == {spec['quality']}",
        manifest.plan_quality.value == spec["quality"],
        f"got {manifest.plan_quality.value}",
        HARD,
    ))

    if spec["quality"] == PlanQuality.NOT_A_FLOORPLAN.value:
        # A rejection must carry a human sentence, not just a verdict.
        reason = (manifest.rejection_reason or "").strip()
        checks.append((f"{name}: rejection explains itself", len(reason) > 20, reason[:70], HARD))
        detail["rejection_reason"] = reason
        return checks, detail

    if spec.get("expect_vector"):
        stats = imaging.pdf_vector_stats(data)
        checks.append((
            f"{name}: recognised as a vector export",
            bool(stats.get("is_vector")),
            f"strokes={stats.get('lines', 0) + stats.get('rects', 0)}",
            SOFT,
        ))

    try:
        graph = agents.run_zones(data)
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
        raise Inconclusive(f"zones: {type(exc).__name__}") from exc

    found = sorted({z.category.value for z in graph.zones})
    unclassified = sum(1 for z in graph.zones if z.category.value == "other")
    detail.update({
        "zone_count": len(graph.zones),
        "categories": found,
        "entrances": graph.entrances,
        "labels": [z.label for z in graph.zones],
        "unclassified": unclassified,
    })

    low, high = spec["zone_count"]
    checks.append((
        f"{name}: zone count in [{low},{high}]",
        low <= len(graph.zones) <= high,
        f"got {len(graph.zones)}",
        SOFT,
    ))
    for category in spec["required_categories"]:
        checks.append(
            (f"{name}: found a '{category}' zone", category in found, f"got {found}", HARD)
        )
    # Without this, the whole set is satisfiable by transcribing the words printed on
    # the drawing — a benchmark that cannot tell classification from OCR.
    checks.append((
        f"{name}: the model actually classified the rooms",
        unclassified <= max(1, len(graph.zones) // 5),
        f"{unclassified} of {len(graph.zones)} left as 'other'",
        HARD,
    ))
    if spec.get("expect_entrance") is not None:
        # Asserted both ways: `false` used to assert nothing at all.
        checks.append((
            f"{name}: entrance found == {spec['expect_entrance']}",
            bool(graph.entrances) == spec["expect_entrance"],
            f"got {graph.entrances}",
            SOFT,
        ))
    # Adjacency is what the flow simulation walks on. Counting edges let six disjoint
    # pairs pass as "connected", so this asks the real question: is every zone linked?
    linked = {zid for pair in graph.adjacency for zid in pair}
    orphans = [z.id for z in graph.zones if z.id not in linked]
    checks.append((
        f"{name}: every zone is connected to another",
        not orphans,
        f"orphans: {orphans[:4]}",
        SOFT,
    ))
    return checks, detail


def main() -> int:
    with open(os.path.join(GOLDEN, "labels.json"), encoding="utf-8") as handle:
        labels = json.load(handle)
    all_checks: list[Check] = []
    details = []
    crashed: list[str] = []

    for name, spec in labels.items():
        try:
            checks, detail = score_plan(name, spec)
        except Inconclusive as exc:
            print(f"\nINCONCLUSIVE: {name} — {exc}. The API was unreachable or throttled;")
            print("this says nothing about the model. Re-run when it settles.")
            return 2
        except Exception as exc:
            # A crash yields ONE failed check instead of the seven-plus it should have
            # produced, which shrinks the denominator and flatters the score: a plan
            # failing outright still averages ~94%. So a crash is fatal on its own.
            crashed.append(name)
            checks = [(f"{name}: agents completed", False, f"{type(exc).__name__}: {str(exc)[:90]}", HARD)]
            detail = {"plan": name, "error": str(exc)[:200]}
        all_checks += checks
        details.append(detail)
        for label, ok, note, hard in checks:
            tag = "PASS" if ok else ("FAIL" if hard else "fail")
            print(f"  [{tag}] {label}" + (f" — {note}" if not ok else ""))

    passed = sum(1 for _l, ok, _n, _h in all_checks if ok)
    score = passed / len(all_checks) if all_checks else 0.0
    hard_failures = [label for label, ok, _n, hard in all_checks if hard and not ok]

    print("\n" + "=" * 70)
    print(f"golden-set score: {passed}/{len(all_checks)} = {score:.1%} (threshold {THRESHOLD:.0%})")
    print(f"plans: {len(labels)} — small by design; add one whenever a real plan surprises us.")
    if crashed:
        print(f"FATAL: {len(crashed)} plan(s) raised instead of being scored: {', '.join(crashed)}")
    if hard_failures:
        print(f"FATAL: {len(hard_failures)} hard check(s) failed — no score excuses these:")
        for label in hard_failures:
            print(f"  · {label}")

    if "--save" in sys.argv:
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "score": round(score, 3),
            "threshold": THRESHOLD,
            "passed": passed,
            "total": len(all_checks),
            "hard_failures": hard_failures,
            "crashed_plans": crashed,
            "models": {"intake": agents.INTAKE_MODEL, "zones": agents.ZONES_MODEL},
            "failures": [{"check": lbl, "detail": n} for lbl, ok, n, _h in all_checks if not ok],
            "plans": details,
        }
        path = os.path.abspath(
            os.path.join(GOLDEN, "..", "..", "..", "..", "artifacts", "golden_set_report.json")
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
        print(f"report written to {path}")

    return 0 if score >= THRESHOLD and not crashed and not hard_failures else 1


if __name__ == "__main__":
    sys.exit(main())
