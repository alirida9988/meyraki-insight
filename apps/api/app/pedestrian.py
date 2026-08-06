"""Guest-flow simulation with JuPedSim (docs/04-REUSE-MAP §3).

Replaces the BFS distance-decay approximation on the simulated track with real
pedestrian dynamics over the plan's actual walkable geometry: a narrow corridor
congests, a wide detour stays usable, and dwell accumulates where guests
genuinely linger — none of which decay-from-the-entrance can express.

Scale: uploaded plans carry no absolute dimensions, so the walkable area is
scaled to a nominal long edge and that assumption travels with the numbers
(project rule: every produced figure shows its basis).

Licensing: JuPedSim is LGPL-3.0 and used as an unmodified pip dependency —
imported in-process, never modified or statically linked (docs/04 §3).
"""

import time

from meyraki_contracts import BACK_OF_HOUSE, ZoneCategory, ZoneGraph

PLAN_LONG_EDGE_M = 30.0   # nominal hospitality floor plate; assumption is reported
MAX_AGENTS = 120
SIM_SECONDS = 45.0
WALL_CLOCK_BUDGET_S = 20.0
SAMPLE_EVERY = 10         # iterations between position samples

# How strongly each zone type attracts a guest heading somewhere.
ATTRACTION = {
    ZoneCategory.DINING: 5.0,
    ZoneCategory.LOUNGE: 4.0,
    ZoneCategory.BAR: 4.0,
    ZoneCategory.RECEPTION: 3.5,
    ZoneCategory.LOBBY: 3.0,
    ZoneCategory.WORKSPACE: 2.5,
    ZoneCategory.MEETING: 2.0,
    ZoneCategory.TERRACE: 2.0,
    ZoneCategory.RESTROOM: 1.5,
    ZoneCategory.CORRIDOR: 0.5,
}


def _zone_polygons(graph: ZoneGraph):
    """Valid shapely polygons in metres, keyed by zone id (invalid ones dropped)."""
    from shapely.geometry import Polygon

    polygons = {}
    for zone in graph.zones:
        points = [(p.x * PLAN_LONG_EDGE_M, p.y * PLAN_LONG_EDGE_M) for p in zone.polygon]
        poly = Polygon(points)
        if not poly.is_valid:
            poly = poly.buffer(0)  # self-intersections from model output
        if poly.is_valid and not poly.is_empty and poly.area > 0.5:
            polygons[zone.id] = poly
    return polygons


def _walkable(polygons: dict):
    """One connected walkable surface; None when the plan cannot form one."""
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union

    if not polygons:
        return None
    # A small buffer bridges hairline gaps between zones the model traced
    # independently, then the negative buffer restores the footprint.
    merged = unary_union([p.buffer(0.12) for p in polygons.values()]).buffer(-0.1)
    if isinstance(merged, MultiPolygon):
        merged = max(merged.geoms, key=lambda g: g.area)  # largest island
    if merged.is_empty or merged.area < 4.0:
        return None
    return merged.simplify(0.05)


def _seed_point(poly, walkable):
    """A point inside both the zone and the walkable surface."""
    for candidate in (poly.centroid, poly.representative_point()):
        if walkable.contains(candidate):
            return (candidate.x, candidate.y)
    fallback = poly.intersection(walkable)
    if fallback.is_empty:
        return None
    point = fallback.representative_point()
    return (point.x, point.y)


Outcome = tuple[dict[str, float] | None, list[str], str | None]


def simulate(graph: ZoneGraph) -> Outcome:
    """(intensities, notes, reason). intensities is None when the simulation declined.

    Declining is a normal outcome, so the third element says *why* in a sentence fit
    for the client's report: the caller used to assert "the geometry could not form a
    walkable surface" no matter the actual cause, which was often simply untrue.
    """
    try:
        import jupedsim as jps
    except Exception:
        return None, [], "the pedestrian simulation engine is unavailable on this server"

    try:
        polygons = _zone_polygons(graph)
        walkable = _walkable(polygons)
        if walkable is None or len(polygons) < 2:
            return None, [], (
                "the traced zones could not be joined into one walkable floor surface"
            )

        entrances = [z for z in graph.entrances if z in polygons]
        if not entrances:  # no entrance traced: start from the largest zone
            entrances = [max(polygons, key=lambda z: polygons[z].area)]

        destinations = [
            zid
            for zid, _poly in polygons.items()
            if zid not in entrances
            and next((z.category for z in graph.zones if z.id == zid), None) not in BACK_OF_HOUSE
        ]
        if not destinations:
            return None, [], (
                "no guest-facing destination was detected on the plan — every zone is "
                "back-of-house or an entrance"
            )

        simulation = jps.Simulation(
            model=jps.CollisionFreeSpeedModel(), geometry=walkable, dt=0.05
        )

        # One journey per destination: walk to it, dwell implicitly, then leave.
        journeys: list[tuple[int, int]] = []  # (journey_id, first_stage_id)
        for zid in destinations:
            seed = _seed_point(polygons[zid], walkable)
            exit_zone = polygons[entrances[0]].intersection(walkable)
            if seed is None or exit_zone.is_empty:
                continue
            waypoint = simulation.add_waypoint_stage(seed, 1.5)
            exit_stage = simulation.add_exit_stage(exit_zone)
            journey = jps.JourneyDescription([waypoint, exit_stage])
            journeys.append((simulation.add_journey(journey), waypoint))
        if not journeys:
            return None, [], "no walkable route was found between the entrance and any zone"

        weights = [
            ATTRACTION.get(
                next((z.category for z in graph.zones if z.id == zid), ZoneCategory.OTHER), 1.0
            )
            for zid in destinations[: len(journeys)]
        ]
        total_weight = sum(weights) or 1.0

        # Spawn guests at the entrance, spread over its area to avoid overlap.
        from shapely.geometry import Point

        entrance_poly = polygons[entrances[0]].intersection(walkable)
        minx, miny, maxx, maxy = entrance_poly.bounds
        placed = 0
        rng_state = 12345  # deterministic: same plan → same simulation
        for i in range(MAX_AGENTS):
            rng_state = (rng_state * 1103515245 + 12345) % (2**31)
            fx = (rng_state % 1000) / 1000
            rng_state = (rng_state * 1103515245 + 12345) % (2**31)
            fy = (rng_state % 1000) / 1000
            point = Point(minx + fx * (maxx - minx), miny + fy * (maxy - miny))
            if not entrance_poly.contains(point):
                continue
            # Weighted journey choice, deterministic across runs.
            pick = (i / MAX_AGENTS) * total_weight
            running = 0.0
            chosen = 0
            for index, weight in enumerate(weights):
                running += weight
                if pick <= running:
                    chosen = index
                    break
            journey_id, first_stage = journeys[chosen]
            try:
                simulation.add_agent(
                    jps.CollisionFreeSpeedModelAgentParameters(
                        position=(point.x, point.y), journey_id=journey_id, stage_id=first_stage
                    )
                )
                placed += 1
            except Exception:
                continue  # too close to another agent or a wall
        if placed < 5:
            return None, [], (
                "too few guests could be placed inside the traced entrance to simulate"
            )

        # Run, sampling where agents actually are.
        from shapely.strtree import STRtree

        zone_ids = list(polygons)
        tree = STRtree([polygons[z] for z in zone_ids])
        dwell = {zid: 0 for zid in zone_ids}
        started = time.monotonic()
        max_iterations = int(SIM_SECONDS / 0.05)
        while (
            simulation.agent_count() > 0
            and simulation.iteration_count() < max_iterations
            and time.monotonic() - started < WALL_CLOCK_BUDGET_S
        ):
            simulation.iterate()
            if simulation.iteration_count() % SAMPLE_EVERY:
                continue
            for agent in simulation.agents():
                point = Point(agent.position[0], agent.position[1])
                for index in tree.query(point):
                    if polygons[zone_ids[index]].contains(point):
                        dwell[zone_ids[index]] += 1
                        break

        peak = max(dwell.values()) if dwell else 0
        if peak == 0:
            return None, [], "no guest reached a destination within the simulated time"
        intensities = {zid: round(count / peak, 3) for zid, count in dwell.items()}
        for zone in graph.zones:  # zones dropped as invalid still need a value
            intensities.setdefault(zone.id, 0.0)
        notes = [
            f"Flow simulated with JuPedSim pedestrian dynamics: {placed} guests over "
            f"{simulation.elapsed_time():.0f}s of modelled time.",
            f"Plan scaled to a nominal {PLAN_LONG_EDGE_M:.0f} m long edge (no scale bar "
            f"was detected), so intensities are relative, not absolute counts.",
        ]
        return intensities, notes, None
    except Exception as exc:
        # Any simulation fault degrades to distance decay, but says what happened.
        return None, [], f"the pedestrian simulation could not complete ({type(exc).__name__})"
