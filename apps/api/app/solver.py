"""Layout feasibility — OR-Tools CP-SAT (docs/04-REUSE-MAP §2, Apache-2.0).

The architecture promises "the model proposes, the solver guarantees". This is
the solver half: every move declares how much of its target zone's floor it
consumes, and CP-SAT finds the largest subset of a scenario's moves that fits
every zone at once, holding back a circulation reserve and protecting egress
zones. A scenario is only marked feasible when all of its moves survive.

It is a multi-zone knapsack, not arithmetic: one move can consume floor in
several zones simultaneously, so which moves conflict depends on the whole set.
"""

from ortools.sat.python import cp_model

from meyraki_contracts import LayoutProposals, Scenario, ZoneCategory, ZoneGraph

from .geometry import polygon_area

# Share of a zone's floor that may be committed to furniture/fixtures. The
# remainder is circulation. Egress-critical zones keep much more clear.
USABLE_SHARE = {
    ZoneCategory.ENTRANCE: 0.35,
    ZoneCategory.CORRIDOR: 0.35,
    ZoneCategory.STAIRS: 0.25,
    ZoneCategory.ELEVATOR: 0.25,
    ZoneCategory.DINING: 0.70,
    ZoneCategory.LOUNGE: 0.70,
    ZoneCategory.BAR: 0.70,
    ZoneCategory.RETAIL: 0.60,   # display units and shelving, densely fitted
    ZoneCategory.LOBBY: 0.60,
    ZoneCategory.GUESTROOM: 0.50,  # bed, wardrobe and ensuite leave little open floor
}
DEFAULT_USABLE_SHARE = 0.65
SCALE = 10_000  # integer area units per unit-square plan


def _zone_capacity(graph: ZoneGraph) -> dict[str, int]:
    caps: dict[str, int] = {}
    for zone in graph.zones:
        units = polygon_area(zone.polygon) * SCALE
        caps[zone.id] = int(units * USABLE_SHARE.get(zone.category, DEFAULT_USABLE_SHARE))
    return caps


def check_scenario(scenario: Scenario, graph: ZoneGraph) -> tuple[bool, list[str]]:
    """(feasible, notes) — feasible only when every move fits simultaneously."""
    caps = _zone_capacity(graph)
    zone_units = {z.id: polygon_area(z.polygon) * SCALE for z in graph.zones}

    demands: list[dict[str, int]] = []
    for move in scenario.moves:
        pct = move.footprint_pct or 0.0
        pct = min(100.0, max(0.0, pct))
        demands.append({zid: int(zone_units.get(zid, 0) * pct / 100) for zid in move.zone_ids})

    if not any(any(d.values()) for d in demands):
        return True, ["No move commits floor area — nothing to contest."]

    model = cp_model.CpModel()
    keep = [model.NewBoolVar(f"m{i}") for i in range(len(scenario.moves))]
    for zone_id, capacity in caps.items():
        terms = [
            demand[zone_id] * keep[i]
            for i, demand in enumerate(demands)
            if demand.get(zone_id)
        ]
        if terms:
            model.Add(sum(terms) <= capacity)
    model.Maximize(sum(keep))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 5.0
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return False, ["Feasibility could not be determined by the solver."]

    dropped = [i for i in range(len(scenario.moves)) if not solver.Value(keep[i])]
    if not dropped:
        return True, [
            f"All {len(scenario.moves)} moves fit within every zone's usable floor "
            f"area (circulation reserve held back)."
        ]
    notes = [
        f"{len(dropped)} of {len(scenario.moves)} moves cannot coexist with the rest "
        f"within the available floor area:"
    ]
    for i in dropped:
        move = scenario.moves[i]
        notes.append(f"· {move.description[:110]} (needs {move.footprint_pct or 0:.0f}% of "
                     f"{', '.join(move.zone_ids)})")
    return False, notes


def validate(proposals: LayoutProposals, graph: ZoneGraph) -> LayoutProposals:
    """Stamp every scenario with a real feasibility verdict."""
    checked = []
    for scenario in proposals.scenarios:
        try:
            feasible, notes = check_scenario(scenario, graph)
        except Exception as exc:  # noqa: BLE001 — a solver fault must not fail the analysis
            feasible, notes = False, [f"Feasibility check unavailable: {str(exc)[:120]}"]
        checked.append(scenario.model_copy(update={"solver_feasible": feasible, "solver_notes": notes}))
    return proposals.model_copy(update={"scenarios": checked})
