"""Real AI agents — Claude vision with structured outputs (M2).

Design contract (docs/01-ARCHITECTURE.md): the model PROPOSES via a loose "wire"
schema; deterministic code validates/repairs into the strict pipeline contracts.
Model tiers per docs/03-TECH-STACK-DECISIONS.md: Haiku 4.5 for intake triage,
Sonnet 5 (high-res vision) for the Zone Analyst.
"""

import anthropic
from pydantic import BaseModel, Field

from meyraki_contracts import (
    FootfallStatus,
    IntakeManifest,
    PlanKind,
    PlanQuality,
    Point,
    SpaceType,
    Zone,
    ZoneCategory,
    ZoneGraph,
)

from . import settings

INTAKE_MODEL = "claude-haiku-4-5"
ZONES_MODEL = "claude-sonnet-5"

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class AgentRefusal(RuntimeError):
    """The model declined the request — surfaced as a failed step, never silence."""


def _plan_block(plan_bytes: bytes) -> dict:
    """Image block for PNG/JPEG, document block for PDF (both vision-readable)."""
    import base64

    data = base64.standard_b64encode(plan_bytes).decode()
    if plan_bytes.startswith(b"%PDF-"):
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    media = "image/png" if plan_bytes.startswith(b"\x89PNG") else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _parse(model: str, max_tokens: int, content: list, output_format: type[BaseModel]):
    response = client().messages.parse(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": content}],
        output_format=output_format,
    )
    if response.stop_reason == "refusal":
        raise AgentRefusal(f"{model} declined the request")
    if response.parsed_output is None:
        raise RuntimeError(f"{model} returned no parseable output")
    return response.parsed_output


# ---------------------------------------------------------------- Intake Agent

class IntakeVision(BaseModel):
    """What the model judges from the plan image alone (footfall is computed by us)."""

    plan_quality: PlanQuality
    plan_kind: PlanKind
    has_scale_hint: bool
    floors_detected: int
    space_type_detected: SpaceType
    warnings: list[str]
    rejection_reason: str | None = None


INTAKE_PROMPT = """You are the intake agent of a spatial-intelligence pipeline for
hospitality interiors. Judge the attached upload:

- plan_quality: "ok" if this is a readable architectural floorplan; "low_res" if it is a
  floorplan but too blurry/small to analyze zones reliably; "not_a_floorplan" if it is
  anything else (photo, chart, text page, logo...).
- plan_kind: "vector_pdf" only for clean CAD-exported PDF linework; else "raster".
- has_scale_hint: true only if a scale bar, dimension line, or measurement text is visible.
- floors_detected: number of distinct floor plates shown (usually 1).
- space_type_detected: best guess of the venue type.
- warnings: short notes an analyst should know (e.g. "labels in French", "heatmap
  overlay already baked into the image").
- rejection_reason: required human-friendly sentence when plan_quality is
  "not_a_floorplan", else null."""


def run_intake(plan_bytes: bytes, footfall: FootfallStatus) -> IntakeManifest:
    vision: IntakeVision = _parse(
        INTAKE_MODEL,
        2048,
        [_plan_block(plan_bytes), {"type": "text", "text": INTAKE_PROMPT}],
        IntakeVision,
    )
    return IntakeManifest(
        plan_quality=vision.plan_quality,
        plan_kind=vision.plan_kind,
        has_scale_hint=vision.has_scale_hint,
        floors_detected=max(1, vision.floors_detected),
        space_type_detected=vision.space_type_detected,
        footfall=footfall,
        warnings=vision.warnings,
        rejection_reason=vision.rejection_reason,
    )


# ---------------------------------------------------------------- Zone Analyst

class WirePoint(BaseModel):
    x: float
    y: float


class WireZone(BaseModel):
    id: str = Field(description="short snake_case identifier, unique")
    category: str = Field(description="one of the allowed category values")
    label: str = Field(description="display name as written on the plan, or a natural name")
    polygon: list[WirePoint] = Field(description="outline in normalized 0-1 coordinates, 4+ points")
    confidence: float


class WireAdjacency(BaseModel):
    a: str
    b: str


class WireZoneGraph(BaseModel):
    zones: list[WireZone]
    adjacency: list[WireAdjacency] = Field(description="pairs of zone ids with a direct opening/door between them")
    entrances: list[str] = Field(description="ids of zones containing a building entrance")


ZONES_PROMPT = f"""You are the Zone Analyst of a spatial-intelligence pipeline for
hospitality interiors. Map every functional zone in the attached floorplan.

Rules:
- Coordinates are normalized: (0,0) is the top-left of the image, (1,1) bottom-right.
- Trace each zone's polygon tightly along its walls — follow the actual room shape,
  not a loose bounding box. Use 4-12 points per polygon.
- category must be one of: {", ".join(c.value for c in ZoneCategory)}.
- Use labels written on the plan when present (any language); otherwise name the zone
  by its evident function (furniture, fixtures).
- adjacency lists pairs of zones connected by a door or open passage.
- entrances lists zones with a door to the outside of the building.
- Cover the full walkable floor area; skip wall voids and shafts.
- confidence: your certainty for that zone in [0,1]."""


def run_zones(plan_bytes: bytes) -> ZoneGraph:
    wire: WireZoneGraph = _parse(
        ZONES_MODEL,
        16000,
        [_plan_block(plan_bytes), {"type": "text", "text": ZONES_PROMPT}],
        WireZoneGraph,
    )
    return repair_zone_graph(wire)


def repair_zone_graph(wire: WireZoneGraph) -> ZoneGraph:
    """Deterministic validation/repair: the model proposes, this code guarantees.

    Clamps coordinates, coerces unknown categories to OTHER, dedupes ids, drops
    degenerate polygons and dangling references — then ZoneGraph's own validator
    has the final word.
    """
    zones: list[Zone] = []
    seen: set[str] = set()
    for wz in wire.zones:
        zid = wz.id.strip().lower().replace(" ", "_") or f"zone_{len(zones)}"
        while zid in seen:
            zid = f"{zid}_{len(zones)}"
        points = [Point(x=min(1.0, max(0.0, p.x)), y=min(1.0, max(0.0, p.y))) for p in wz.polygon]
        if len(points) < 3:
            continue
        try:
            category = ZoneCategory(wz.category.strip().lower())
        except ValueError:
            category = ZoneCategory.OTHER
        seen.add(zid)
        zones.append(
            Zone(
                id=zid,
                category=category,
                label=wz.label.strip() or zid,
                polygon=points,
                confidence=min(1.0, max(0.0, wz.confidence)),
            )
        )

    adjacency = []
    for pair in wire.adjacency:
        a, b = pair.a.strip().lower().replace(" ", "_"), pair.b.strip().lower().replace(" ", "_")
        if a in seen and b in seen and a != b and (a, b) not in adjacency and (b, a) not in adjacency:
            adjacency.append((a, b))
    entrances = [e.strip().lower().replace(" ", "_") for e in wire.entrances]
    entrances = [e for e in dict.fromkeys(entrances) if e in seen]

    return ZoneGraph(zones=zones, adjacency=adjacency, entrances=entrances)
