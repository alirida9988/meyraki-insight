"""The 7-step agent pipeline runner (M1 skeleton).

Steps are stubs producing schema-valid placeholder outputs; M2 replaces each body
with the real agent while the runner, persistence, and resumability stay unchanged.
Every output is validated against its contract before being stored — the "no bugs"
boundary exists from day one.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import BaseModel
from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from meyraki_contracts import (
    BusinessCase,
    Assumption,
    ExecutionPlan,
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
    QAVerdict,
    ReportArtifact,
    Scenario,
    StepBudget,
    Track,
    Zone,
    ZoneCategory,
    ZoneGraph,
)

from . import flow as flow_mod
from . import heatmap, settings, storage
from .models import Analysis, Event, StepRun, Upload

STEP_NAMES = [
    "intake",
    "routing",
    "zones",
    "flow",
    "layout",
    "moodboard",
    "business",
    "report",
    "qa",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(session: Session, analysis_id: str, kind: str, message: str) -> None:
    session.add(Event(analysis_id=analysis_id, kind=kind, message=message))
    session.commit()


class Ctx:
    """Accumulated step outputs, keyed by step name."""

    def __init__(self, analysis: Analysis, session: Session):
        self.analysis = analysis
        self.session = session
        self.outputs: dict[str, dict] = {}

    def upload_bytes(self, upload_id: str | None) -> bytes | None:
        if upload_id is None:
            return None
        upload = self.session.get(Upload, upload_id)
        return storage.load(upload.storage_key) if upload else None


# ---------------------------------------------------------------- steps

def step_intake(ctx: Ctx) -> IntakeManifest:
    footfall = FootfallStatus.VALID if ctx.analysis.footfall_upload_id else FootfallStatus.NONE
    if settings.agents_enabled():
        from . import agents

        plan_bytes = ctx.upload_bytes(ctx.analysis.floorplan_upload_id)
        if plan_bytes is not None:
            return agents.run_intake(plan_bytes, footfall)
    # Stub fallback (no API key / MEYRAKI_USE_AGENTS=off): trusts the upload validation.
    return IntakeManifest(
        plan_quality=PlanQuality.OK,
        plan_kind=PlanKind.RASTER,
        space_type_detected=ctx.analysis.project.space_type,  # type: ignore[arg-type]
        footfall=footfall,
    )


def step_routing(ctx: Ctx) -> ExecutionPlan:
    track = Track.DATA_DRIVEN if ctx.analysis.footfall_upload_id else Track.SIMULATED
    steps = [s for s in STEP_NAMES if s not in ("intake", "routing")]
    return ExecutionPlan(
        track=track,
        steps=steps,
        budgets=[StepBudget(step=s, model="stub", max_usd=0.2) for s in steps],
    )


def step_zones(ctx: Ctx) -> ZoneGraph:
    if settings.agents_enabled():
        from . import agents

        plan_bytes = ctx.upload_bytes(ctx.analysis.floorplan_upload_id)
        if plan_bytes is not None:
            return agents.run_zones(plan_bytes)
    # Stub fallback: fixed demo zones.
    square = lambda x0, y0, x1, y1: [  # noqa: E731
        Point(x=x0, y=y0), Point(x=x1, y=y0), Point(x=x1, y=y1), Point(x=x0, y=y1)
    ]
    return ZoneGraph(
        zones=[
            Zone(id="entrance", category=ZoneCategory.ENTRANCE, label="Entrance",
                 polygon=square(0.0, 0.7, 0.25, 1.0), confidence=0.9),
            Zone(id="lobby", category=ZoneCategory.LOBBY, label="Lobby",
                 polygon=square(0.0, 0.0, 0.5, 0.7), confidence=0.9),
            Zone(id="cafe", category=ZoneCategory.DINING, label="Café",
                 polygon=square(0.5, 0.0, 1.0, 0.6), confidence=0.85),
        ],
        adjacency=[("entrance", "lobby"), ("lobby", "cafe")],
        entrances=["entrance"],
    )


def step_flow(ctx: Ctx) -> FlowReport:
    """Real implementation: measured footfall join, or simulated distance decay."""
    plan = ExecutionPlan.model_validate(ctx.outputs["routing"])
    graph = ZoneGraph.model_validate(ctx.outputs["zones"])

    if plan.track == Track.DATA_DRIVEN:
        footfall_bytes = ctx.upload_bytes(ctx.analysis.footfall_upload_id)
        report = (
            flow_mod.data_driven(graph, footfall_bytes)
            if footfall_bytes is not None
            else flow_mod.simulated(graph)
        )
    else:
        report = flow_mod.simulated(graph)

    plan_bytes = ctx.upload_bytes(ctx.analysis.floorplan_upload_id)
    if plan_bytes is not None:
        png = heatmap.render(plan_bytes, graph, report)
        if png is not None:
            report.heatmap_key = storage.save(png, ".png")
        else:
            report.notes.append("Heatmap preview unavailable for PDF plans yet — arriving with rasterization (M3).")
    return report


def step_layout(ctx: Ctx) -> LayoutProposals:
    objectives = [Objective(o) for o in ctx.analysis.objectives] or [Objective.GUEST_FLOW]
    if settings.agents_enabled():
        from . import agents

        return agents.run_layout(
            ZoneGraph.model_validate(ctx.outputs["zones"]),
            FlowReport.model_validate(ctx.outputs["flow"]),
            objectives,
            ctx.analysis.project.space_type,
            ctx.analysis.brief,
        )
    return LayoutProposals(
        objectives=objectives,
        scenarios=[
            Scenario(
                id="a",
                name="Open lobby",
                moves=[LayoutMove(
                    description="Relocate reception desk to the north wall",
                    zone_ids=["lobby"],
                    rationale="Clears the entrance sightline and splits the queue from through-traffic.",
                )],
                predicted_effects={"guest_flow": "+15%"},
                confidence=0.7,
            )
        ],
    )


def step_moodboard(ctx: Ctx) -> Moodboard:
    if settings.agents_enabled():
        from . import agents

        objectives = [Objective(o) for o in ctx.analysis.objectives] or [Objective.GUEST_FLOW]
        graph = ZoneGraph.model_validate(ctx.outputs["zones"])
        board = agents.run_moodboard(
            ctx.analysis.project.space_type, objectives, ctx.analysis.brief, graph
        )
        keys, errors = agents.generate_moodboard_images(
            board.style_name, board.materials, ctx.analysis.project.space_type, graph
        )
        if errors:
            # Visible, never fatal: renders are an enhancement over palette/materials.
            note = "quota/billing" if "429" in errors[0] else errors[0][:80]
            _emit(
                ctx.session,
                ctx.analysis.id,
                "step",
                f"moodboard: {len(keys)}/3 renders generated ({note}) — continuing",
            )
        return board.model_copy(update={"image_keys": keys})
    return Moodboard(
        style_name="Serene boutique",
        palette=["#FBFAF7", "#1C4A3E", "#B08D57"],
        materials=["travertine", "walnut", "linen"],
    )


# Guest-facing zones drive the flow-efficiency score; back-of-house is excluded.
BACK_OF_HOUSE = {
    ZoneCategory.KITCHEN,
    ZoneCategory.STORAGE,
    ZoneCategory.SERVICE,
    ZoneCategory.RESTROOM,
    ZoneCategory.STAIRS,
    ZoneCategory.ELEVATOR,
}


def flow_efficiency_score(graph: ZoneGraph, flow: FlowReport) -> float | None:
    """Area-weighted mean flow intensity across guest-facing zones, on 0-100.
    Deterministic math — the model never invents this number."""
    from .geometry import polygon_area

    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    weighted = total = 0.0
    for zone in graph.zones:
        if zone.category in BACK_OF_HOUSE:
            continue
        area = polygon_area(zone.polygon)
        weighted += area * intensity.get(zone.id, 0.0)
        total += area
    if total == 0:
        return None
    return round(100 * weighted / total, 1)


def step_business(ctx: Ctx) -> BusinessCase:
    graph = ZoneGraph.model_validate(ctx.outputs["zones"])
    flow = FlowReport.model_validate(ctx.outputs["flow"])
    score = flow_efficiency_score(graph, flow)
    assumptions = [
        Assumption(
            statement="Flow Efficiency Score = area-weighted mean flow intensity across "
            "guest-facing zones (back-of-house excluded), scaled to 0-100.",
            source="deterministic",
        ),
        Assumption(
            statement=(
                "Intensities are measured from uploaded footfall data."
                if flow.track == Track.DATA_DRIVEN
                else "Intensities are simulated (distance decay from entrances) — upload footfall data for measured values."
            ),
            source="pipeline",
        ),
        Assumption(
            statement="Revenue-per-sqm projections require venue revenue baselines "
            "(pending founder presets per space type) — not yet computed.",
            source="preset",
        ),
    ]
    return BusinessCase(flow_efficiency_score=score, assumptions=assumptions)


def step_report(ctx: Ctx) -> ReportArtifact:
    graph = ZoneGraph.model_validate(ctx.outputs["zones"])
    flow = FlowReport.model_validate(ctx.outputs["flow"])
    layout = LayoutProposals.model_validate(ctx.outputs["layout"])
    moodboard = Moodboard.model_validate(ctx.outputs["moodboard"])
    business = ctx.outputs["business"]
    project = ctx.analysis.project

    if settings.agents_enabled():
        from . import agents

        wire = agents.run_report(
            project.name,
            project.client_name,
            project.space_type,
            ctx.outputs["intake"],
            graph,
            flow,
            layout,
            moodboard,
            business,
        )
        narrative = wire.model_dump()
    else:
        # Stub path (tests/no key): no narrative, no PDF — report_key stays None.
        return ReportArtifact(language="en", sections=["summary", "zones", "flow", "layout", "design"])

    from . import pdf as pdf_mod
    from . import report_html

    heatmap_png = storage.load(flow.heatmap_key) if flow.heatmap_key else None
    moodboard_pngs = [storage.load(k) for k in moodboard.image_keys[:3]]
    html = report_html.build_html(
        project_name=project.name,
        client_name=project.client_name,
        space_type=project.space_type,
        narrative=narrative,
        graph=graph,
        flow=flow,
        layout=layout,
        moodboard=moodboard,
        business=business,
        heatmap_png=heatmap_png,
        moodboard_pngs=moodboard_pngs,
    )
    key = storage.save(pdf_mod.html_to_pdf(html), ".pdf")
    return ReportArtifact(
        report_key=key,
        language="en",
        sections=["summary", "zones", "flow", "layout", "design", "next_steps", "assumptions"],
    )


def step_qa(ctx: Ctx) -> QAVerdict:
    zone_ids = {z["id"] for z in ctx.outputs["zones"]["zones"]}
    referenced = {
        zid
        for s in ctx.outputs["layout"]["scenarios"]
        for m in s["moves"]
        for zid in m["zone_ids"]
    } | set(ctx.outputs["flow"]["bottlenecks"]) | set(ctx.outputs["flow"]["dead_zones"])
    unknown = referenced - zone_ids
    if unknown:
        from meyraki_contracts import QAIssue

        return QAVerdict(
            passed=False,
            issues=[QAIssue(step="layout", severity="block",
                            description=f"References unknown zones: {sorted(unknown)}")],
            rerun_steps=["layout"],
        )
    return QAVerdict(passed=True)


STEPS: dict[str, Callable[[Ctx], BaseModel]] = {
    "intake": step_intake,
    "routing": step_routing,
    "zones": step_zones,
    "flow": step_flow,
    "layout": step_layout,
    "moodboard": step_moodboard,
    "business": step_business,
    "report": step_report,
    "qa": step_qa,
}


# A 'running' analysis whose heartbeat is older than this is considered crashed
# and may be re-claimed by resume or another runner.
STALE_AFTER = timedelta(minutes=5)


def claimable_where(analysis_id: str, statuses: tuple[str, ...], now: datetime):
    """WHERE clause matching an analysis that is safe to (re)claim: in one of the
    given statuses, or 'running' with a stale/missing heartbeat (crashed worker)."""
    return (
        Analysis.id == analysis_id,
        or_(
            Analysis.status.in_(statuses),
            and_(
                Analysis.status == "running",
                or_(
                    Analysis.heartbeat_at.is_(None),
                    Analysis.heartbeat_at < now - STALE_AFTER,
                ),
            ),
        ),
    )


def run_analysis(session: Session, analysis_id: str) -> None:
    """Execute pending steps in order; completed steps are skipped (resume-safe).

    The atomic UPDATE below is the concurrency lock: of N runners scheduled for the
    same analysis (double-click, retry storm, resume race), exactly one sees
    rowcount == 1 and executes; the rest return immediately.
    """
    now = _now()
    claimed = session.execute(
        update(Analysis)
        .where(*claimable_where(analysis_id, ("queued",), now))
        .values(status="running", heartbeat_at=now)
    ).rowcount
    session.commit()
    if claimed != 1:
        return
    session.expire_all()
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        return
    _emit(session, analysis.id, "pipeline", "Analysis started")

    ctx = Ctx(analysis, session)
    try:
        for step in analysis.steps:
            if step.status == "done" and step.output is not None:
                ctx.outputs[step.name] = step.output
            else:
                step.status = "running"
                step.started_at = _now()
                analysis.heartbeat_at = _now()
                session.commit()
                _emit(session, analysis.id, "step", f"{step.name}: started")

                output = STEPS[step.name](ctx)  # raises on contract violation
                step.output = output.model_dump(mode="json")
                step.status = "done"
                step.finished_at = _now()
                session.commit()
                ctx.outputs[step.name] = step.output
                _emit(session, analysis.id, "step", f"{step.name}: done")

            # Path-independent: fires whether intake just ran or was loaded from a
            # prior (possibly crashed) run — a non-floorplan must never continue.
            if (
                step.name == "intake"
                and ctx.outputs["intake"].get("plan_quality") == PlanQuality.NOT_A_FLOORPLAN.value
            ):
                analysis.status = "rejected"
                analysis.error = ctx.outputs["intake"].get("rejection_reason")
                session.commit()
                _emit(session, analysis.id, "pipeline", "Rejected at intake")
                return

        qa = QAVerdict.model_validate(ctx.outputs["qa"])
        analysis.status = "done" if qa.passed else "failed"
        if not qa.passed:
            analysis.error = "; ".join(i.description for i in qa.issues)
        session.commit()
        _emit(session, analysis.id, "pipeline", f"Analysis {analysis.status}")
    except Exception as exc:  # noqa: BLE001 — step failures must land in the DB, not the void
        for step in analysis.steps:
            if step.status == "running":
                step.status = "failed"
                step.error = str(exc)
        analysis.status = "failed"
        analysis.error = str(exc)
        session.commit()
        _emit(session, analysis.id, "pipeline", f"Analysis failed: {exc}")
