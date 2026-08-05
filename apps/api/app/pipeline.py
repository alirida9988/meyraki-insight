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
    ZoneFlow,
    ZoneGraph,
)

from .models import Analysis, Event, StepRun

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

    def __init__(self, analysis: Analysis):
        self.analysis = analysis
        self.outputs: dict[str, dict] = {}


# ---------------------------------------------------------------- stub steps (M2 replaces bodies)

def step_intake(ctx: Ctx) -> IntakeManifest:
    return IntakeManifest(
        plan_quality=PlanQuality.OK,
        plan_kind=PlanKind.RASTER,
        space_type_detected=ctx.analysis.project.space_type,  # type: ignore[arg-type]
        footfall=FootfallStatus.VALID if ctx.analysis.footfall_upload_id else FootfallStatus.NONE,
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
    plan = ExecutionPlan.model_validate(ctx.outputs["routing"])
    return FlowReport(
        track=plan.track,
        zone_flows=[
            ZoneFlow(zone_id="entrance", intensity=0.6),
            ZoneFlow(zone_id="lobby", intensity=0.9, is_bottleneck=True),
            ZoneFlow(zone_id="cafe", intensity=0.3, is_dead_zone=True),
        ],
        bottlenecks=["lobby"],
        dead_zones=["cafe"],
    )


def step_layout(ctx: Ctx) -> LayoutProposals:
    objectives = [Objective(o) for o in ctx.analysis.objectives] or [Objective.GUEST_FLOW]
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
    return Moodboard(
        style_name="Serene boutique",
        palette=["#FBFAF7", "#1C4A3E", "#B08D57"],
        materials=["travertine", "walnut", "linen"],
    )


def step_business(ctx: Ctx) -> BusinessCase:
    return BusinessCase(
        flow_efficiency_score=68,
        assumptions=[Assumption(
            statement="Boutique-hotel preset revenue baselines",
            source="preset",
        )],
    )


def step_report(ctx: Ctx) -> ReportArtifact:
    # M4: real step renders the branded PDF and stores its key.
    return ReportArtifact(language="en", sections=["zones", "flow", "layout", "moodboard", "business"])


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

    ctx = Ctx(analysis)
    try:
        for step in analysis.steps:
            if step.status == "done" and step.output is not None:
                ctx.outputs[step.name] = step.output
                continue
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

            if step.name == "intake" and output.plan_quality == PlanQuality.NOT_A_FLOORPLAN:  # type: ignore[union-attr]
                analysis.status = "rejected"
                analysis.error = output.rejection_reason  # type: ignore[union-attr]
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
