"""Flow Analyst — the real implementation (M2).

Data-driven track: joins the uploaded footfall CSV onto detected zones.
Simulated track: JuPedSim pedestrian dynamics (app/pedestrian.py), falling back
to BFS distance-decay over the adjacency graph when the traced geometry cannot
form a walkable surface. Both paths say which one produced the numbers.
"""

import csv
import io
import unicodedata
from collections import deque

from meyraki_contracts import FlowReport, Track, ZoneFlow, ZoneGraph

BOTTLENECK_AT = 0.8
DEAD_ZONE_AT = 0.2


def _norm(name: str) -> str:
    """Casefold + strip accents/spaces so 'Café' joins 'cafe'."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().replace(" ", "")


def data_driven(graph: ZoneGraph, footfall_csv: bytes) -> FlowReport:
    totals: dict[str, int] = {}
    reader = csv.DictReader(io.StringIO(footfall_csv.decode("utf-8-sig")))
    for row in reader:
        key = _norm(row.get("zone_name") or "")
        count = (row.get("traffic_count") or "0").strip()
        if key and count.isdigit():
            totals[key] = totals.get(key, 0) + int(count)

    by_key: dict[str, str] = {}
    for z in graph.zones:
        for candidate in (z.label, z.id, z.category.value):
            by_key.setdefault(_norm(candidate), z.id)

    zone_totals: dict[str, int] = {z.id: 0 for z in graph.zones}
    unmatched: list[str] = []
    for key, total in totals.items():
        zone_id = by_key.get(key)
        if zone_id is None:
            unmatched.append(key)
        else:
            zone_totals[zone_id] += total

    peak = max(zone_totals.values()) if any(zone_totals.values()) else 0
    flows = [
        _flow(zid, (total / peak) if peak else 0.0)
        for zid, total in zone_totals.items()
    ]
    notes = (
        [f"Footfall zones not found on the plan (ignored): {', '.join(sorted(unmatched))}"]
        if unmatched
        else []
    )
    return _report(Track.DATA_DRIVEN, flows, notes)


def simulated(graph: ZoneGraph) -> FlowReport:
    """Pedestrian dynamics when the geometry allows it, distance decay otherwise."""
    from . import pedestrian

    result = pedestrian.simulate(graph)
    if result is not None:
        intensities, notes = result
        flows = [_flow(zid, value) for zid, value in intensities.items()]
        return _report(Track.SIMULATED, flows, notes)
    return distance_decay(graph)


def distance_decay(graph: ZoneGraph) -> FlowReport:
    """Fallback: intensity falls with adjacency-graph distance from an entrance."""
    depth: dict[str, int] = {e: 0 for e in graph.entrances}
    neighbors: dict[str, list[str]] = {z.id: [] for z in graph.zones}
    for a, b in graph.adjacency:
        neighbors[a].append(b)
        neighbors[b].append(a)

    queue = deque(graph.entrances)
    while queue:
        current = queue.popleft()
        for nxt in neighbors[current]:
            if nxt not in depth:
                depth[nxt] = depth[current] + 1
                queue.append(nxt)

    flows = [
        _flow(z.id, max(0.15, 1.0 - 0.25 * depth[z.id]) if z.id in depth else 0.15)
        for z in graph.zones
    ]
    notes = [
        "Simulated flow (distance decay from entrances, used because the traced "
        "geometry could not form a walkable surface for pedestrian simulation) — "
        "upload footfall data for measured intensities."
    ]
    return _report(Track.SIMULATED, flows, notes)


def _flow(zone_id: str, intensity: float) -> ZoneFlow:
    intensity = round(min(1.0, max(0.0, intensity)), 3)
    return ZoneFlow(
        zone_id=zone_id,
        intensity=intensity,
        is_bottleneck=intensity >= BOTTLENECK_AT,
        is_dead_zone=intensity <= DEAD_ZONE_AT,
    )


def _report(track: Track, flows: list[ZoneFlow], notes: list[str]) -> FlowReport:
    return FlowReport(
        track=track,
        zone_flows=flows,
        bottlenecks=[f.zone_id for f in flows if f.is_bottleneck],
        dead_zones=[f.zone_id for f in flows if f.is_dead_zone],
        notes=notes,
    )
