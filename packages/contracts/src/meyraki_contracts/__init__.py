"""Typed contracts for the Meyraki Insight agent pipeline.

Every agent step consumes and produces exactly one of these models; the pipeline
runner validates outputs against them before the next step runs. docs/01-ARCHITECTURE.md
describes the step semantics. JSON Schemas exported from here are the single source
for the TypeScript types used by the web app.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

CONTRACT_VERSION = "0.1.0"


class SpaceType(StrEnum):
    HOTEL = "hotel"
    CAFE = "cafe"
    RESTAURANT = "restaurant"
    COWORKING = "coworking"
    OFFICE = "office"
    CLINIC = "clinic"
    GALLERY = "gallery"
    OTHER = "other"


class Objective(StrEnum):
    GUEST_FLOW = "guest_flow"
    SEATING_EFFICIENCY = "seating_efficiency"
    REVENUE_PER_SQM = "revenue_per_sqm"
    AMBIANCE = "ambiance"


class ZoneCategory(StrEnum):
    ENTRANCE = "entrance"
    RECEPTION = "reception"
    LOBBY = "lobby"
    LOUNGE = "lounge"
    DINING = "dining"
    BAR = "bar"
    KITCHEN = "kitchen"
    CORRIDOR = "corridor"
    STAIRS = "stairs"
    ELEVATOR = "elevator"
    RESTROOM = "restroom"
    TERRACE = "terrace"
    WORKSPACE = "workspace"
    MEETING = "meeting"
    STORAGE = "storage"
    SERVICE = "service"
    RETAIL = "retail"
    """A concession inside the venue — gift shop, florist, barber, spa boutique. Added
    2026-08-08 after a real hotel plan put three of them in `other`: they are revenue
    tenants with their own dwell behaviour, not unclassifiable space."""
    GUESTROOM = "guestroom"
    """A let bedroom or suite. Added 2026-08-08: `hotel` is the product's flagship space
    type and a guest floor is mostly guest rooms, yet there was no category for one, so
    every room fell to `other` and drew the default attraction of 1.0 against dining's
    5.0 — the simulation routed guests away from the single largest destination on the
    floor. Same failure as the café that was typed `other`, except no prompt could fix
    it, because the right answer did not exist in the taxonomy."""
    OTHER = "other"


# Two different questions, so two sets — they used to be three copies in three modules
# that quietly disagreed about restrooms, stairs and lifts.
BACK_OF_HOUSE: frozenset[ZoneCategory] = frozenset(
    {ZoneCategory.KITCHEN, ZoneCategory.STORAGE, ZoneCategory.SERVICE}
)
"""Staff-only. Never a guest destination in a flow simulation."""

UTILITY_ZONES: frozenset[ZoneCategory] = frozenset(
    {ZoneCategory.RESTROOM, ZoneCategory.STAIRS, ZoneCategory.ELEVATOR}
)
"""Guests do go here, but traffic through a lavatory or a lift core is not a measure
of how well the space performs, so these sit outside the flow-efficiency score."""

PRIVATE_DESTINATIONS: frozenset[ZoneCategory] = frozenset({ZoneCategory.GUESTROOM})
"""Guest-facing, but let to one party and entered only by them. A guest room is a
destination the simulation must route to — it is the point of a hotel floor — yet how
heavily it is occupied says nothing about whether the space is well laid out, and on a
guest floor it would dominate the score by area and drown out the circulation it is
supposed to measure. So it attracts guests fully and scores not at all."""

SCORE_EXCLUDED: frozenset[ZoneCategory] = BACK_OF_HOUSE | UTILITY_ZONES | PRIVATE_DESTINATIONS
"""Excluded from the Flow Efficiency Score by CATEGORY. Named in the report's assumptions.

Not sufficient on its own — see `is_guest_facing`, which also honours `Zone.staff_only`.
"""


def is_guest_facing(zone: "Zone") -> bool:
    """Whether a guest can be in this room, which is what the score is about.

    Two independent reasons a room is not guest space: what it is for (a kitchen, a lift
    core) and who is allowed in it (a staff canteen). Category answers only the first, so
    anything deciding "does this count as guest space" must ask here rather than testing
    SCORE_EXCLUDED directly — a hotel basement is entirely staff space and almost none of
    it is back-of-house by category.
    """
    return not zone.staff_only and zone.category not in SCORE_EXCLUDED


# ---------------------------------------------------------------- step 1: intake

class PlanQuality(StrEnum):
    OK = "ok"
    LOW_RES = "low_res"
    NOT_A_FLOORPLAN = "not_a_floorplan"


class PlanKind(StrEnum):
    RASTER = "raster"
    VECTOR_PDF = "vector_pdf"


class FootfallStatus(StrEnum):
    NONE = "none"
    VALID = "valid"
    SCHEMA_ERRORS = "schema_errors"


class IntakeManifest(BaseModel):
    plan_quality: PlanQuality
    plan_kind: PlanKind
    has_scale_hint: bool = False
    floors_detected: int = Field(1, ge=1)
    space_type_detected: SpaceType = SpaceType.OTHER
    footfall: FootfallStatus = FootfallStatus.NONE
    footfall_errors: list[str] = []
    warnings: list[str] = []
    rejection_reason: str | None = None
    """Human-readable reason when plan_quality == NOT_A_FLOORPLAN; pipeline stops."""


# ---------------------------------------------------------------- step 2: routing

class Track(StrEnum):
    DATA_DRIVEN = "data_driven"
    SIMULATED = "simulated"


class StepBudget(BaseModel):
    step: str
    model: str
    max_usd: float = Field(gt=0)


class ExecutionPlan(BaseModel):
    track: Track
    steps: list[str]
    budgets: list[StepBudget]
    total_budget_usd: float = Field(1.50, gt=0)


# ---------------------------------------------------------------- step 3: zones

class Point(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Zone(BaseModel):
    id: str
    category: ZoneCategory
    label: str
    polygon: list[Point] = Field(min_length=3)
    area_sqm: float | None = Field(None, gt=0)
    confidence: float = Field(ge=0, le=1)
    staff_only: bool = False
    """Who may enter, which is orthogonal to what the room is for.

    Added 2026-08-11 after a real hotel basement was analysed. Category describes
    function and had no way to say audience, so a staff canteen was correctly typed
    `dining`, an engineers' office `workspace` and a service corridor `corridor` — and all
    three were then scored as guest space. The result was a confident flow-efficiency
    number computed over rooms no guest can enter.

    A flag rather than new categories, because a staff canteen genuinely is dining: adding
    STAFF_DINING, STAFF_LOCKER and STAFF_CORRIDOR would duplicate the function axis for
    every value it already has. Defaults to False, so a plan whose analyst says nothing
    behaves exactly as before.
    """


class ZoneGraph(BaseModel):
    zones: list[Zone]
    adjacency: list[tuple[str, str]] = []
    entrances: list[str] = []
    walkable_mask_key: str | None = None
    """Object-storage key of the rendered walkable-space mask."""

    @model_validator(mode="after")
    def _ids_consistent(self) -> "ZoneGraph":
        ids = {z.id for z in self.zones}
        if len(ids) != len(self.zones):
            raise ValueError("zone ids must be unique")
        for a, b in self.adjacency:
            if a not in ids or b not in ids:
                raise ValueError(f"adjacency references unknown zone: {(a, b)}")
        unknown = [e for e in self.entrances if e not in ids]
        if unknown:
            raise ValueError(f"entrances reference unknown zones: {unknown}")
        return self


# ---------------------------------------------------------------- step 4a: flow

class ZoneFlow(BaseModel):
    zone_id: str
    intensity: float = Field(ge=0, le=1)
    is_bottleneck: bool = False
    is_dead_zone: bool = False


class FlowReport(BaseModel):
    track: Track
    zone_flows: list[ZoneFlow]
    bottlenecks: list[str] = []
    dead_zones: list[str] = []
    peak_window: str | None = None
    heatmap_key: str | None = None
    notes: list[str] = []


# ---------------------------------------------------------------- step 4b: layout

class LayoutMove(BaseModel):
    description: str
    zone_ids: list[str]
    rationale: str
    footprint_pct: float | None = Field(
        None, ge=0, le=100,
        description="Share of the target zone's floor area this move occupies; "
        "0/None for signage, policy or wayfinding changes.",
    )


class Scenario(BaseModel):
    id: str
    name: str
    moves: list[LayoutMove] = Field(min_length=1)
    predicted_effects: dict[str, str]
    """e.g. {"guest_flow": "+15%", "idle_wait": "-3 min"}"""
    confidence: float = Field(ge=0, le=1)
    solver_feasible: bool = False
    """Set by the OR-Tools CP-SAT check (app/solver.py), never by the model."""
    solver_notes: list[str] = []
    """Human-readable verdict: what fits, or which moves conflict and why."""


class LayoutProposals(BaseModel):
    scenarios: list[Scenario] = Field(min_length=1, max_length=3)
    objectives: list[Objective]


# ---------------------------------------------------------------- step 4c: moodboard

class Moodboard(BaseModel):
    style_name: str
    palette: list[str] = Field(min_length=3, description="hex colors")
    materials: list[str]
    furniture_notes: list[str] = []
    lighting_concept: str | None = None
    image_keys: list[str] = []
    render_provider: str | None = None
    """Which image model produced the renders — travels to the report, not just the log."""
    renders_are_draft: bool = False
    """True when the renders came from the free fallback provider. The client report
    must caption them as draft: they indicate mood and material direction, but they are
    visibly softer than the contracted provider's output and are not final visuals."""


# ---------------------------------------------------------------- step 5: business

class Assumption(BaseModel):
    statement: str
    source: str
    """Where the number comes from — preset, user input, or simulation."""


class BusinessCase(BaseModel):
    currency: str = "USD"
    roi_per_sqm_current: float | None = None
    roi_per_sqm_projected: dict[str, float] = {}
    """Keyed by scenario id."""
    flow_efficiency_score: float | None = Field(None, ge=0, le=100)
    payback_estimate_months: dict[str, float] = {}
    assumptions: list[Assumption] = Field(min_length=1)
    """Every number ships with its assumptions — non-negotiable."""


# ---------------------------------------------------------------- step 6: report

class ReportArtifact(BaseModel):
    report_key: str | None = None
    """Object-storage key of the rendered PDF; None until the real writer lands (M4)."""
    language: str = "en"
    sections: list[str] = []


# ---------------------------------------------------------------- step 7: QA

class QAIssue(BaseModel):
    step: str
    severity: str  # "block" | "warn"
    description: str


class QAVerdict(BaseModel):
    passed: bool
    issues: list[QAIssue] = []
    rerun_steps: list[str] = []


ALL_CONTRACTS: dict[str, type[BaseModel]] = {
    m.__name__: m
    for m in (
        IntakeManifest,
        ExecutionPlan,
        ZoneGraph,
        FlowReport,
        LayoutProposals,
        Moodboard,
        BusinessCase,
        ReportArtifact,
        QAVerdict,
    )
}


def json_schemas() -> dict[str, dict]:
    """JSON Schema per contract — the source for generated TypeScript types."""
    return {name: model.model_json_schema() for name, model in ALL_CONTRACTS.items()}
