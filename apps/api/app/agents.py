"""Real AI agents — Claude vision with structured outputs (M2).

Design contract (docs/01-ARCHITECTURE.md): the model PROPOSES via a loose "wire"
schema; deterministic code validates/repairs into the strict pipeline contracts.
Model tiers per docs/03-TECH-STACK-DECISIONS.md: Haiku 4.5 for intake triage,
Sonnet 5 (high-res vision) for the Zone Analyst.
"""

import anthropic
import pydantic
from pydantic import BaseModel, Field

from meyraki_contracts import (
    SCORE_EXCLUDED,
    FlowReport,
    FootfallStatus,
    IntakeManifest,
    LayoutMove,
    LayoutProposals,
    Moodboard,
    Objective,
    PlanKind,
    PlanQuality,
    Point,
    Scenario,
    SpaceType,
    Zone,
    ZoneCategory,
    ZoneGraph,
)

from . import costs, settings

INTAKE_MODEL = "claude-haiku-4-5"
ZONES_MODEL = "claude-sonnet-5"
LAYOUT_MODEL = "claude-opus-5"
MOODBOARD_MODEL = "claude-sonnet-5"

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


class AgentRefusal(RuntimeError):
    """The model declined the request — surfaced as a failed step, never silence."""


def _plan_block(plan_bytes: bytes) -> dict:
    """Vision block for a plan: PDFs go as documents, rasters are normalized
    first (a 300 DPI A1 scan exceeds the API's 8000px limit outright)."""
    import base64

    from . import imaging

    kind, payload = imaging.vision_payload(plan_bytes)
    data = base64.standard_b64encode(payload).decode()
    if kind == "document":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        }
    media = "image/png" if payload.startswith(b"\x89PNG") else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _parse(model: str, max_tokens: int, content: list, output_format: type[BaseModel],
           step: str = "agent"):
    # The one chokepoint every agent routes through, so the kill switch lives here.
    # MEYRAKI_USE_AGENTS=off used to be honoured only by the pipeline, which meant a
    # test calling an agent directly would happily bill a real key from .env.
    if not settings.agents_enabled():
        raise RuntimeError(
            "model call attempted while agents are disabled (MEYRAKI_USE_AGENTS=off "
            "or no API key) — the offline suite must never reach the network"
        )
    try:
        response = client().messages.parse(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
            output_format=output_format,
        )
    except pydantic.ValidationError as exc:
        # Billed and thrown away. A truncated response consumed the full output budget,
        # so book the worst case rather than $0 — this is precisely the runaway the
        # ceiling exists to stop, and recording nothing made it invisible to the guard.
        costs.record_tokens(step, model, 0, max_tokens)
        # messages.parse() validates inside the SDK call, so a response truncated at
        # max_tokens surfaces here as "Invalid JSON: EOF while parsing" long before the
        # stop_reason check below can explain it. Arabic reports hit this first: the
        # same text costs several times more tokens than its English equivalent.
        truncated = "EOF while parsing" in str(exc) or "Invalid JSON" in str(exc)
        detail = (
            f"output truncated at max_tokens={max_tokens} — raise the step's budget"
            if truncated
            else "output did not satisfy the contract"
        )
        raise RuntimeError(f"{model}: {detail} ({output_format.__name__})") from exc
    # Book usage the moment the call returns: the three raises below all follow a call
    # that was already billed, and recording after them booked those runaway cases at $0.
    usage = getattr(response, "usage", None)
    if usage is None:
        costs.record_usage_unavailable(step, model)
    else:
        costs.record_tokens(
            step, model,
            getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0,
        )
    if response.stop_reason == "refusal":
        raise AgentRefusal(f"{model} declined the request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"{model} output truncated at max_tokens — raise the step's budget")
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
        step="intake",
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
- Classify by function, not by wording: a café/restaurant/breakfast room is dining,
  a beverage counter is bar, an office or desk area is workspace, and a shop, boutique,
  florist, barber or newsstand inside the venue is retail. Use "other" only when the
  function genuinely cannot be determined.
- adjacency lists pairs of zones connected by a door or open passage.
- entrances lists zones with a door to the outside of the building.
- Cover the full walkable floor area; skip wall voids and shafts.
- If the sheet shows SEVERAL floor plates side by side (a typical-floor plan, a
  mezzanine, a basement, a ground floor...), map ONE plate only: the ground or entrance
  level if it is present, otherwise the largest. Everything downstream — the heatmap,
  the flow simulation, the layout solver — describes a single floor plate, so zones
  traced across four stacked plans are meaningless whichever way they are drawn.
- confidence: your certainty for that zone in [0,1]."""


# 16000, not higher: the SDK refuses a non-streaming request whose max_tokens implies a
# generation longer than ten minutes, so 24000 raised ValueError on EVERY zones call —
# a production-only break, invisible to a suite that never contacts a model.
# test_agent_budgets_stay_within_the_sdks_non_streaming_limit guards it offline.
# A multi-plate architect's sheet is handled by scoping the prompt to one floor plate,
# which is the real fix; more tokens would only have bought a meaningless answer.
ZONES_MAX_TOKENS = 16000


def run_zones(plan_bytes: bytes) -> ZoneGraph:
    wire: WireZoneGraph = _parse(
        ZONES_MODEL,
        ZONES_MAX_TOKENS,
        [_plan_block(plan_bytes), {"type": "text", "text": ZONES_PROMPT}],
        WireZoneGraph,
        step="zones",
    )
    return repair_zone_graph(wire)


def repair_zone_graph(wire: WireZoneGraph) -> ZoneGraph:
    """Deterministic validation/repair: the model proposes, this code guarantees.

    Clamps coordinates, coerces unknown categories to OTHER, dedupes ids, drops
    degenerate polygons and dangling references — then ZoneGraph's own validator
    has the final word.

    It deliberately does NOT guess a category from the zone's printed label. That was
    tried and reverted: the prompt change at ZONES_PROMPT ("classify by function, not
    by wording") fixed the café-typed-as-other case on its own, while keyword matching
    mis-filed a GCC VIP majlis ("صالة كبار" contains "بار") as a bar, "Stockholm Suite"
    as storage, and a swimming pool ("حمام سباحة") as a restroom — each of which
    deletes a guest zone from the flow score and the simulation. OTHER is a safe
    default; a wrong category is not. tests/golden_set.py is what catches a
    regression here.
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

    if not zones:
        raise RuntimeError(
            "Zone Analyst found no usable zones — the plan may be unreadable or too abstract"
        )
    return ZoneGraph(zones=zones, adjacency=adjacency, entrances=entrances)


# ---------------------------------------------------------------- Layout Optimizer

class WireMove(BaseModel):
    description: str = Field(description="one concrete physical change")
    zone_ids: list[str] = Field(description="ids of the zones this move touches")
    rationale: str = Field(description="why, grounded in the flow data")
    footprint_pct: float = Field(
        description="percentage of the target zone's floor area this move occupies "
        "(0 for signage, policy, wayfinding or door-swing changes)"
    )


class WireScenario(BaseModel):
    id: str
    name: str
    moves: list[WireMove]
    predicted_effects: dict[str, str] = Field(
        description='metric -> effect, e.g. {"guest_flow": "+12% (est.)"}'
    )
    confidence: float


class WireLayout(BaseModel):
    scenarios: list[WireScenario] = Field(description="2-3 distinct scenarios")


def _zone_brief(graph: ZoneGraph, flow: FlowReport) -> str:
    from .geometry import polygon_area

    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    lines = []
    for z in graph.zones:
        share = round(polygon_area(z.polygon) * 100, 1)
        lines.append(
            f"- {z.id} ({z.category.value}, '{z.label}'): {share}% of plan area, "
            f"flow intensity {intensity.get(z.id, 0.0)}"
        )
    lines.append(f"Adjacency: {graph.adjacency}")
    lines.append(f"Entrances: {graph.entrances}")
    lines.append(f"Bottlenecks: {flow.bottlenecks} | Dead zones: {flow.dead_zones}")
    return "\n".join(lines)


def run_layout(
    graph: ZoneGraph,
    flow: FlowReport,
    objectives: list[Objective],
    space_type: str,
    brief: str | None,
) -> LayoutProposals:
    prompt = f"""You are the Layout Optimizer of a spatial-intelligence pipeline for
hospitality interiors. Propose layout changes for this {space_type}.

Zones and measured/simulated flow:
{_zone_brief(graph, flow)}

Client objectives: {", ".join(o.value for o in objectives)}
Client brief: {brief or "(none)"}

Produce 2-3 DISTINCT scenarios. Rules:
- Each move is one concrete, physically actionable change ("relocate the reception desk
  to the north wall of reception", "convert the dead lounge corner into a 6-seat
  banquette"). No vague advice.
- zone_ids must only use ids from the list above.
- rationale must cite the flow data (bottleneck, dead zone, adjacency) that motivates it.
- footprint_pct: honest estimate of how much of that zone's floor the change occupies.
  A 6-seat banquette in a small lounge might be 25; a wayfinding sign is 0. A
  constraint solver checks these against each zone's usable area, so inflating them
  will get the move rejected as infeasible.
- predicted_effects values are honest estimates and MUST carry their basis, e.g.
  "+10-15% (est. from rebalancing lobby bottleneck)". Never a bare number.
- Scenario ids: short snake_case. confidence in [0,1] per scenario.
- Scenarios should differ in strategy (e.g. circulation-first vs revenue-first), not
  be variations of one idea."""
    def attempt(text: str) -> LayoutProposals | None:
        # NOTE: _parse sits outside the try so AgentRefusal/truncation propagate —
        # only repair_layout's "zero valid scenarios" is retryable.
        wire: WireLayout = _parse(LAYOUT_MODEL, 16000, [{"type": "text", "text": text}], WireLayout, step="layout")
        try:
            return repair_layout(wire, graph, objectives)
        except RuntimeError:
            return None  # zero valid scenarios — caller decides whether to retry

    first = attempt(prompt)
    if first is not None and len(first.scenarios) >= 2:
        return first

    # One bounded retry with explicit feedback — the product promises 2-3 scenarios.
    survived = len(first.scenarios) if first else 0
    retry_prompt = (
        prompt
        + f"\n\nIMPORTANT: your previous answer yielded only {survived} valid scenario(s) "
        "after validation. Moves whose zone_ids are not EXACTLY the ids listed above are "
        "discarded. Produce 2-3 distinct scenarios, every move using exact zone ids."
    )
    # The step ceiling is checked between steps, but this step can make two Opus calls
    # at max_tokens=16000 — enough to overshoot the whole analysis budget inside one
    # step. So the retry is gated too: if the first attempt already spent the step's
    # allowance, keep what it produced rather than paying twice for the same step.
    try:
        costs.check_budget("layout", costs.current_budget_usd())
    except costs.BudgetExceeded:
        if first is not None:
            return first
        raise

    # A failing RETRY must never discard a valid first attempt (review F4);
    # with no first attempt, the retry's error is the real signal — propagate it.
    if first is not None:
        try:
            second = attempt(retry_prompt)
        except Exception:
            return first
        return max((first, second) if second is not None else (first,),
                   key=lambda p: len(p.scenarios))
    second = attempt(retry_prompt)
    if second is None:
        raise RuntimeError("Layout Optimizer produced no scenario with valid zone references")
    return second


def repair_layout(
    wire: WireLayout, graph: ZoneGraph, objectives: list[Objective]
) -> LayoutProposals:
    """Deterministic guarantee: every surviving move references real zones only.
    # ponytail: geometric feasibility (OR-Tools clearances/capacity) lands with
    # furniture-level data in Phase 2 — solver_feasible stays False until then.
    """
    valid_ids = {z.id for z in graph.zones}
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for ws in wire.scenarios:
        moves = [
            LayoutMove(
                description=m.description.strip(),
                zone_ids=[z for z in (i.strip().lower().replace(" ", "_") for i in m.zone_ids) if z in valid_ids],
                rationale=m.rationale.strip(),
                footprint_pct=min(100.0, max(0.0, m.footprint_pct)),
            )
            for m in ws.moves
            if m.description.strip()
        ]
        moves = [m for m in moves if m.zone_ids]
        if not moves:
            continue
        sid = ws.id.strip().lower().replace(" ", "_") or f"scenario_{len(scenarios)}"
        while sid in seen:
            sid = f"{sid}_{len(scenarios)}"
        seen.add(sid)
        scenarios.append(
            Scenario(
                id=sid,
                name=ws.name.strip() or sid,
                moves=moves,
                predicted_effects=ws.predicted_effects,
                confidence=min(1.0, max(0.0, ws.confidence)),
                solver_feasible=False,
            )
        )
        if len(scenarios) == 3:
            break
    if not scenarios:
        raise RuntimeError("Layout Optimizer produced no scenario with valid zone references")
    return LayoutProposals(scenarios=scenarios, objectives=objectives)


# ---------------------------------------------------------------- Moodboard Designer

class WireMoodboard(BaseModel):
    style_name: str
    palette: list[str] = Field(description="4-6 hex colors like #1C4A3E, cohesive")
    materials: list[str] = Field(description="3-6 physical materials")
    furniture_notes: list[str] = Field(description="2-5 concrete furniture directions")
    lighting_concept: str


_HEX = __import__("re").compile(r"^#?[0-9a-fA-F]{6}$")


def clean_palette(raw: list[str]) -> list[str]:
    """Validated, normalized, deduped hex palette; fails loudly below 3 colors."""
    palette: list[str] = []
    for c in raw:
        c = c.strip()
        if _HEX.match(c):
            c = c.upper() if c.startswith("#") else f"#{c.upper()}"
            c = "#" + c.lstrip("#")
            if c not in palette:
                palette.append(c)
    if len(palette) < 3:
        raise RuntimeError(f"Moodboard Designer returned an unusable palette: {raw}")
    return palette


def run_moodboard(
    space_type: str, objectives: list[Objective], brief: str | None, graph: ZoneGraph
) -> Moodboard:
    zones = ", ".join(f"{z.label} ({z.category.value})" for z in graph.zones)
    prompt = f"""You are the Moodboard Designer of a spatial-intelligence pipeline for
hospitality interiors. Define a design direction for this {space_type}.

Zones present: {zones}
Client objectives: {", ".join(o.value for o in objectives)}
Client brief: {brief or "(none)"}

Rules:
- style_name: a specific, evocative direction (not "modern" alone).
- palette: 4-6 cohesive hex colors, ordered dominant -> accent.
- materials: real, sourceable materials fitting the style and a hospitality budget.
- furniture_notes: concrete directions a buyer could act on.
- lighting_concept: one sentence, layered lighting for the key zones."""
    wire: WireMoodboard = _parse(
        MOODBOARD_MODEL, 4096, [{"type": "text", "text": prompt}], WireMoodboard,
        step="moodboard",
    )
    palette = clean_palette(wire.palette)
    return Moodboard(
        style_name=wire.style_name.strip(),
        palette=palette[:6],
        materials=[m.strip() for m in wire.materials if m.strip()][:6],
        furniture_notes=[f.strip() for f in wire.furniture_notes if f.strip()][:5],
        lighting_concept=wire.lighting_concept.strip() or None,
        image_keys=[],  # renders are orchestrated by the pipeline step (graceful degrade)
    )


# Zones worth naming in a hero shot: guest-facing, and somewhere a guest lingers.
# This used to be a third hardcoded list that disagreed with the other two — it omitted
# meeting rooms — so it now derives from the shared vocabulary in the contracts package.
HERO_SHOT_ZONES = frozenset(ZoneCategory) - SCORE_EXCLUDED - {
    ZoneCategory.ENTRANCE, ZoneCategory.CORRIDOR, ZoneCategory.OTHER
}


def render_prompts(style: str, materials: list[str], space_type: str, graph: ZoneGraph) -> list[str]:
    guest_zones = [z.label for z in graph.zones if z.category in HERO_SHOT_ZONES][:4]
    base = (
        f"Photorealistic interior design render of a {space_type}, {style} style. "
        f"Materials: {', '.join(materials)}. Natural light, editorial photography, "
        "no people, no text, no watermarks."
    )
    return [
        f"{base} Wide hero shot of the main guest area ({', '.join(guest_zones) or 'lobby'}).",
        f"{base} Intimate detail vignette: seating corner with lighting and material textures.",
        f"{base} Arrival view from the entrance looking into the space.",
    ]


def generate_moodboard_images(
    style: str, materials: list[str], space_type: str, graph: ZoneGraph
) -> tuple[list[str], list[str], list["imagegen.Render"]]:
    """3 interior renders → (storage keys, errors, the Render records).

    The Render records carry provider and pixel size so the caller can caption
    draft-quality output in the client report instead of passing it off as final.

    Renders are an enhancement: the caller decides how to surface failures —
    never by failing the analysis, never silently (pipeline emits a register note).
    """
    from . import imagegen, storage

    import time

    keys: list[str] = []
    errors: list[str] = []
    renders: list[imagegen.Render] = []
    started = time.monotonic()
    prompts = render_prompts(style, materials, space_type, graph)
    for index, prompt in enumerate(prompts):
        elapsed = time.monotonic() - started
        if elapsed > imagegen.RENDER_BUDGET_S:
            errors.append(
                f"render budget of {imagegen.RENDER_BUDGET_S:.0f}s reached after "
                f"{len(keys)} render(s) — remaining {len(prompts) - index} skipped"
            )
            break
        # The free provider refuses back-to-back requests (measured: 429 at 0s, 3s and
        # 6s gaps), which is why only one of three renders used to survive. Space them —
        # including after a failed attempt, since a rate limit is the likeliest cause.
        # Paid providers do not need it, so a run served by FLUX or Gemini pays nothing.
        # `or errors` matters: renders 1 and 2 can succeed on a paid provider (no
        # spacing needed) and then render 3 drops to the free tier mid-batch. Without
        # this it arrives with no cooldown and is refused, which is exactly what
        # happened when FLUX ran out of credits partway through a batch.
        used_free = (
            not renders
            or bool(errors)
            or any(r.provider in imagegen.NEEDS_COOLDOWN for r in renders)
        )
        if index and used_free and imagegen.FREE_COOLDOWN_S:
            time.sleep(imagegen.FREE_COOLDOWN_S)
        try:
            render = imagegen.generate_render(prompt)
            keys.append(storage.save(render.data, render.suffix))
            renders.append(render)
        except Exception as exc:  # noqa: BLE001 — collected for the caller
            errors.append(str(exc)[:500])
    return keys, errors, renders


# ---------------------------------------------------------------- Report Writer

REPORT_MODEL = "claude-sonnet-5"
# 4096 was enough for English and truncated Arabic mid-sentence (caught by the
# Arabic E2E run): the same report costs several times more tokens in Arabic.
REPORT_MAX_TOKENS = 16000


class WireReport(BaseModel):
    executive_summary: str = Field(description="3-5 sentences, client-facing, leads with the outcome")
    zone_findings: str = Field(description="what the space consists of and what stands out, 2-4 sentences")
    flow_findings: str = Field(description="where guests concentrate, bottlenecks, dead zones, 2-4 sentences")
    layout_recommendation: str = Field(
        description="which scenario to start with and why, referencing the data, 3-5 sentences"
    )
    design_direction: str = Field(description="the moodboard direction in client language, 2-3 sentences")
    next_steps: list[str] = Field(description="3-5 concrete actions, ordered")


def run_report(
    project_name: str,
    client_name: str | None,
    space_type: str,
    intake: dict,
    graph: ZoneGraph,
    flow: FlowReport,
    layout: LayoutProposals,
    moodboard: Moodboard,
    business: dict,
    language: str = "en",
) -> WireReport:
    from .geometry import polygon_area

    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    zone_lines = "\n".join(
        f"- {z.label} ({z.category.value}): {round(polygon_area(z.polygon) * 100, 1)}% of plan, "
        f"intensity {intensity.get(z.id, 0.0)}"
        for z in graph.zones
    )
    scenario_lines = "\n".join(
        f"- {s.name} (confidence {s.confidence}): " + "; ".join(m.description for m in s.moves)
        for s in layout.scenarios
    )
    language_rule = (
        "Write ALL fields in Modern Standard Arabic — formal business register, the "
        "voice of a senior Gulf consultancy. Keep numerals as Latin digits (37.1, 18%). "
        "Zone names may stay as written on the plan."
        if language == "ar"
        else "British or international English."
    )
    prompt = f"""You are the Report Writer of a spatial-intelligence pipeline. Write the
client-facing narrative for a branded insight report. Voice: measured, confident,
specific — a senior consultant, never salesy. {language_rule}

Project: {project_name} — a {space_type}{f" for {client_name}" if client_name else ""}
Intake notes: {intake.get("warnings", [])}
Flow track: {"measured footfall data" if flow.track.value == "data_driven" else "simulated flow (no footfall data provided)"}
Flow Efficiency Score: {business.get("flow_efficiency_score")}

Zones:
{zone_lines}

Bottlenecks: {flow.bottlenecks} | Dead zones: {flow.dead_zones}

Proposed scenarios:
{scenario_lines}

Design direction: {moodboard.style_name}; materials {", ".join(moodboard.materials)}

Rules:
- Ground every claim in the data above; no invented numbers.
- Where flow is simulated, say so plainly once.
- next_steps must be actions the client can schedule, ordered by leverage."""
    return _parse(REPORT_MODEL, REPORT_MAX_TOKENS, [{"type": "text", "text": prompt}],
                  WireReport, step="report")
