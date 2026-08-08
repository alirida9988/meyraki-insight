"""Prompt contract tests — the last open quality gate in `docs/05-BUILD-PLAN.md`.

Every agent prompt, run on three seeds, must return output that satisfies its contract.
That much the SDK already enforces via `output_format`, which is exactly why a
pass/fail-only version of this test would be theatre: it goes green until the day it
doesn't.

The failure this actually hunts is **truncation**. When a response hits `max_tokens` the
JSON stops mid-object and surfaces as `Invalid JSON: EOF while parsing` — a contract
error that names the schema and says nothing about the budget. The Arabic report hit it
in production: the same document costs several times more output tokens in Arabic than in
English, and the English seed passed comfortably the whole time.

So each run records how much of the output budget the prompt actually consumed, and a
seed that finishes inside its ceiling but with less than MARGIN to spare is reported as a
warning. That is the signal a binary test cannot give you: the prompt that works today
and truncates on a slightly larger venue tomorrow.

Seeds are chosen to be the worst realistic case rather than the average — the largest
zone graph, every objective at once, Arabic — because a contract test on easy inputs
measures nothing.

    .venv/bin/python tests/prompt_contracts.py           # run and report
    .venv/bin/python tests/prompt_contracts.py --save    # also write artifacts/

Exit codes: 0 pass · 1 fail · 2 inconclusive (the API was unreachable, rate limited or
overloaded — not a prompt regression, and it must never be reported as one).

Opt-in by design: it spends model credits, so it never joins the offline pytest suite.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("MEYRAKI_USE_AGENTS", "auto")

import anthropic  # noqa: E402

from app import agents, costs, imaging  # noqa: E402
from meyraki_contracts import (  # noqa: E402
    FlowReport,
    FootfallStatus,
    IntakeManifest,
    LayoutMove,
    LayoutProposals,
    Moodboard,
    Objective,
    Point,
    Scenario,
    Track,
    Zone,
    ZoneCategory,
    ZoneFlow,
    ZoneGraph,
)

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
# tests/ -> api/ -> apps/ -> repo root. Deliverables belong in artifacts/ at the ROOT,
# per the founder directive; three levels landed them in apps/artifacts/.
ARTIFACTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "artifacts",
)

# A prompt that consumes more than 75% of its output budget is one bigger venue away from
# truncating. Not a failure — the run succeeded — but the thing worth knowing before a
# client uploads a hotel twice this size.
MARGIN = 0.25


class Inconclusive(RuntimeError):
    """The API was unreachable or rate limited. Not a prompt regression."""


# ---------------------------------------------------------------- ceiling capture

# The ceilings live in agents.py as a mix of module constants and inline literals.
# Copying them here would create a second source of truth that goes quietly stale the
# first time someone raises one — and a headroom test measuring against a stale ceiling
# reports fiction. Instead, record the value _parse is actually called with.
_CEILINGS: dict[str, int] = {}
_real_parse = agents._parse


def _recording_parse(model, max_tokens, content, output_format, step="agent"):
    _CEILINGS[step] = max_tokens
    return _real_parse(model, max_tokens, content, output_format, step=step)


agents._parse = _recording_parse


def _plan(name: str) -> bytes:
    with open(os.path.join(GOLDEN, name), "rb") as handle:
        data = handle.read()
    if imaging.is_pdf(data):
        raster = imaging.plan_raster(data)
        if raster is None:
            raise Inconclusive(f"could not rasterize {name}")
        return raster
    return data


# ---------------------------------------------------------------- fixtures

def _graph(size: str) -> ZoneGraph:
    """A deterministic zone graph, built rather than read from a model.

    Feeding layout/moodboard/report the output of a real zones call would cost more and
    test less: the input would vary run to run, so a failure could not be attributed to
    the prompt under test.
    """
    def square(zid, cat, label, x0, y0, x1, y1):
        return Zone(
            id=zid, category=cat, label=label, confidence=0.9,
            polygon=[Point(x=x0, y=y0), Point(x=x1, y=y0), Point(x=x1, y=y1), Point(x=x0, y=y1)],
        )

    small = [
        square("entrance", ZoneCategory.ENTRANCE, "Entrance", 0.0, 0.8, 0.2, 1.0),
        square("lobby", ZoneCategory.LOBBY, "Lobby", 0.0, 0.0, 0.5, 0.8),
        square("dining", ZoneCategory.DINING, "Restaurant", 0.5, 0.0, 1.0, 0.5),
        square("retail", ZoneCategory.RETAIL, "Gift Shop", 0.5, 0.5, 1.0, 1.0),
    ]
    if size == "small":
        return ZoneGraph(
            zones=small,
            adjacency=[("entrance", "lobby"), ("lobby", "dining"), ("lobby", "retail")],
            entrances=["entrance"],
        )

    # The stress case: a full-service hotel floor. Every downstream prompt scales its
    # output with the zone count, so this is where a ceiling gets found.
    cats = [
        (ZoneCategory.RECEPTION, "Reception"), (ZoneCategory.LOUNGE, "Lounge"),
        (ZoneCategory.BAR, "Lobby Bar"), (ZoneCategory.DINING, "All-Day Dining"),
        (ZoneCategory.WORKSPACE, "Business Centre"), (ZoneCategory.RESTROOM, "Restrooms"),
        (ZoneCategory.CORRIDOR, "Corridor"), (ZoneCategory.STORAGE, "Back of House"),
    ]
    big = list(small)
    for i, (cat, label) in enumerate(cats):
        col, row = i % 4, i // 4
        big.append(square(f"z{i}", cat, label,
                          col * 0.25, 0.5 + row * 0.25, col * 0.25 + 0.24, 0.5 + row * 0.25 + 0.24))
    return ZoneGraph(
        zones=big,
        adjacency=[("entrance", "lobby")] + [("lobby", f"z{i}") for i in range(len(cats))],
        entrances=["entrance"],
    )


def _flow(graph: ZoneGraph) -> FlowReport:
    flows, bottlenecks, dead = [], [], []
    for i, zone in enumerate(graph.zones):
        intensity = round(0.15 + (i * 0.37) % 0.85, 2)
        is_bottleneck = intensity > 0.85
        is_dead = intensity < 0.25
        flows.append(ZoneFlow(zone_id=zone.id, intensity=intensity,
                              is_bottleneck=is_bottleneck, is_dead_zone=is_dead))
        if is_bottleneck:
            bottlenecks.append(zone.id)
        if is_dead:
            dead.append(zone.id)
    return FlowReport(track=Track.SIMULATED, zone_flows=flows, bottlenecks=bottlenecks,
                      dead_zones=dead, notes=["simulated pedestrian dynamics (JuPedSim)"])


ALL_OBJECTIVES = list(Objective)


def _layout(graph: ZoneGraph) -> LayoutProposals:
    return agents.run_layout(graph, _flow(graph), ALL_OBJECTIVES, "hotel",
                             "Raise covers without losing the lobby's calm.")


# ---------------------------------------------------------------- the seeds

def seed_intake_plan():
    return agents.run_intake(_plan("cleo_hotel.png"), FootfallStatus.NONE)


def seed_intake_scan():
    return agents.run_intake(_plan("blackstone_hotel_1910.png"), FootfallStatus.NONE)


def seed_intake_not_a_plan():
    # A rejection is a valid contract outcome, and the one the product most depends on.
    return agents.run_intake(_plan("sales_chart.png"), FootfallStatus.VALID)


def seed_zones_plan():
    return agents.run_zones(_plan("cleo_hotel.png"))


def seed_zones_scan():
    return agents.run_zones(_plan("blackstone_hotel_1910.png"))


def seed_zones_vector():
    return agents.run_zones(_plan("cafe_vector.pdf"))


def seed_layout_small():
    return _layout(_graph("small"))


def seed_layout_large():
    return _layout(_graph("large"))


def seed_layout_single_objective():
    graph = _graph("large")
    return agents.run_layout(graph, _flow(graph), [Objective.GUEST_FLOW], "restaurant", None)


def seed_moodboard_hotel():
    return agents.run_moodboard("hotel", ALL_OBJECTIVES, "Warm minimalism, GCC clientele.",
                                _graph("large"))


def seed_moodboard_cafe():
    return agents.run_moodboard("cafe", [Objective.GUEST_FLOW], None, _graph("small"))


def seed_moodboard_no_brief():
    return agents.run_moodboard("coworking", ALL_OBJECTIVES, None, _graph("large"))


def _proposals() -> LayoutProposals:
    """The contract allows 1–3 scenarios, so the fixture carries the maximum.

    The report writes a passage per scenario per move, which is exactly the axis that
    drives its output length — an empty or single-scenario fixture would understate the
    token cost of a real deliverable and make the headroom reading useless.
    """
    return LayoutProposals(
        objectives=ALL_OBJECTIVES,
        scenarios=[
            Scenario(
                id=f"s{i}", name=name,
                moves=[
                    LayoutMove(description=desc, zone_ids=["lobby", "dining"],
                               rationale=why, footprint_pct=pct)
                    for desc, why, pct in moves
                ],
                predicted_effects={"guest_flow": "+12% (est.)",
                                   "revenue_per_sqm": "+7% (est.)"},
                confidence=0.7, solver_feasible=(i != 2),
            )
            for i, (name, moves) in enumerate([
                ("Open the lobby sightline", [
                    ("Relocate the reception desk to the north wall",
                     "Splits queueing from through-traffic.", 8.0),
                    ("Turn the lounge seating 90 degrees toward the courtyard",
                     "Recovers the dead corner behind the columns.", 6.0),
                ]),
                ("Extend all-day dining into the atrium", [
                    ("Add sixteen covers along the glazed edge",
                     "Converts circulation surplus into revenue area.", 12.0),
                ]),
                ("Relocate the bar to the atrium centre", [
                    ("Move the bar island to the plan centroid",
                     "Shortens the mean walk from every seating cluster.", 15.0),
                ]),
            ])
        ],
    )


def _report(graph: ZoneGraph, language: str):
    return agents.run_report(
        project_name="Hotel Cleo", client_name="Cleo Hospitality", space_type="hotel",
        intake={"space_type": "hotel", "plan_quality": "good"},
        graph=graph, flow=_flow(graph),
        layout=_proposals(),
        moodboard=Moodboard(style_name="Serene Boutique",
                            palette=["#FBFAF7", "#1C4A3E", "#B08D57"],
                            materials=["travertine", "walnut", "linen"]),
        business={"flow_efficiency_score": 71.4, "assumptions": []},
        language=language,
    )


def seed_report_en():
    return _report(_graph("large"), "en")


def seed_report_ar():
    # The known truncation case: Arabic costs several times the output tokens of the same
    # English document, and it is the differentiator, so it gets the largest graph.
    return _report(_graph("large"), "ar")


def seed_report_small():
    return _report(_graph("small"), "en")


SEEDS: list[tuple[str, str, callable]] = [
    ("intake", "clean plan", seed_intake_plan),
    ("intake", "1910 scan", seed_intake_scan),
    ("intake", "not a floorplan", seed_intake_not_a_plan),
    ("zones", "clean plan", seed_zones_plan),
    ("zones", "1910 scan", seed_zones_scan),
    ("zones", "vector PDF", seed_zones_vector),
    ("layout", "4 zones", seed_layout_small),
    ("layout", "12 zones, every objective", seed_layout_large),
    ("layout", "one objective", seed_layout_single_objective),
    ("moodboard", "hotel, full brief", seed_moodboard_hotel),
    ("moodboard", "cafe, minimal", seed_moodboard_cafe),
    ("moodboard", "coworking, no brief", seed_moodboard_no_brief),
    ("report", "English, 12 zones", seed_report_en),
    ("report", "Arabic, 12 zones", seed_report_ar),
    ("report", "English, 4 zones", seed_report_small),
]

EXPECTED = {
    "intake": IntakeManifest,
    "zones": ZoneGraph,
    "layout": LayoutProposals,
    "moodboard": Moodboard,
    "report": None,  # WireReport, repaired downstream — identity checked by field presence
}


# ---------------------------------------------------------------- the run

def run_seed(step: str, label: str, fn) -> dict:
    _CEILINGS.pop(step, None)
    token = costs.start_ledger()
    try:
        try:
            result = fn()
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            raise Inconclusive(f"{step}/{label}: {type(exc).__name__}") from exc

        entries = [e for e in costs.entries() if e["step"] == step]
        out_tokens = sum(e["output_tokens"] for e in entries)
        usd = sum(e["usd"] for e in entries)
    finally:
        costs.stop_ledger(token)

    ceiling = _CEILINGS.get(step)
    used = (out_tokens / ceiling) if ceiling else None
    expected = EXPECTED.get(step)

    outcome = {
        "step": step, "seed": label, "ok": True, "note": "",
        "output_tokens": out_tokens, "ceiling": ceiling,
        "used_pct": round(used * 100, 1) if used is not None else None,
        "usd": round(usd, 4),
    }
    if expected is not None and not isinstance(result, expected):
        outcome["ok"] = False
        outcome["note"] = f"returned {type(result).__name__}, expected {expected.__name__}"
    elif used is not None and used > 1 - MARGIN:
        outcome["note"] = (
            f"only {round((1 - used) * 100)}% of the output budget left — raise "
            f"the ceiling before a larger venue truncates it"
        )
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="write artifacts/prompt-contracts.json")
    parser.add_argument("--step", help="run only one agent's seeds")
    args = parser.parse_args()

    seeds = [s for s in SEEDS if not args.step or s[0] == args.step]
    results, inconclusive = [], []

    for step, label, fn in seeds:
        try:
            outcome = run_seed(step, label, fn)
        except Inconclusive as exc:
            inconclusive.append(str(exc))
            print(f"  ?  {step:<10} {label:<28} inconclusive: {exc}")
            continue
        except Exception as exc:  # a contract failure is the point of the test
            results.append({"step": step, "seed": label, "ok": False, "note": str(exc)[:200],
                            "output_tokens": 0, "ceiling": _CEILINGS.get(step),
                            "used_pct": None, "usd": 0.0})
            print(f"  ✗  {step:<10} {label:<28} {str(exc)[:90]}")
            continue

        results.append(outcome)
        mark = "✓" if outcome["ok"] else "✗"
        budget = f"{outcome['used_pct']}% of {outcome['ceiling']}" if outcome["ceiling"] else "—"
        warn = "  ⚠ " + outcome["note"] if outcome["ok"] and outcome["note"] else ""
        print(f"  {mark}  {step:<10} {label:<28} {budget:>18}  ${outcome['usd']:.4f}{warn}")

    failed = [r for r in results if not r["ok"]]
    warned = [r for r in results if r["ok"] and r["note"]]
    total = sum(r["usd"] for r in results)

    print(f"\n{len(results) - len(failed)}/{len(results)} seeds satisfied their contract "
          f"· ${total:.4f} spent")
    if warned:
        print(f"{len(warned)} within {int(MARGIN * 100)}% of the output ceiling:")
        for r in warned:
            print(f"  · {r['step']}/{r['seed']}: {r['note']}")
    if failed:
        print(f"{len(failed)} FAILED:")
        for r in failed:
            print(f"  · {r['step']}/{r['seed']}: {r['note']}")

    if args.save:
        os.makedirs(ARTIFACTS, exist_ok=True)
        path = os.path.join(ARTIFACTS, "prompt-contracts.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                       "margin": MARGIN, "total_usd": round(total, 4),
                       "results": results, "inconclusive": inconclusive}, handle, indent=2)
        print(f"\nwrote {path}")

    if inconclusive and not results:
        print(f"\nINCONCLUSIVE — {len(inconclusive)} seeds could not reach the API")
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
