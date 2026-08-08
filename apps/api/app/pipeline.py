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
    SCORE_EXCLUDED,
    Scenario,
    StepBudget,
    Track,
    Zone,
    ZoneCategory,
    ZoneGraph,
)

from . import flow as flow_mod
from . import costs, heatmap, settings, storage
from .models import Analysis, CostEntry, Event, StepRun, Upload

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
            manifest = agents.run_intake(plan_bytes, footfall)
            # Deterministic evidence wins over the model's guess for plan_kind:
            # pdfplumber can see whether the PDF actually carries vector geometry.
            from . import imaging

            if imaging.is_pdf(plan_bytes):
                stats = imaging.pdf_vector_stats(plan_bytes)
                if stats.get("readable"):
                    kind = PlanKind.VECTOR_PDF if stats["is_vector"] else PlanKind.RASTER
                    note = (
                        f"PDF carries {stats['lines']} lines / {stats['rects']} rects — "
                        + ("true vector export" if stats["is_vector"] else "scanned image inside a PDF")
                    )
                    manifest = manifest.model_copy(
                        update={"plan_kind": kind, "warnings": [*manifest.warnings, note]}
                    )
            return manifest
    # Stub fallback (no API key / MEYRAKI_USE_AGENTS=off): trusts the upload validation.
    return IntakeManifest(
        plan_quality=PlanQuality.OK,
        plan_kind=PlanKind.RASTER,
        space_type_detected=ctx.analysis.project.space_type,  # type: ignore[arg-type]
        footfall=footfall,
    )


# What each step actually bills, so the execution plan stops claiming "stub" for steps
# that spend real money. Deterministic steps bill nothing.
STEP_MODELS = {
    "zones": "claude-sonnet-5",
    "layout": "claude-opus-5",
    "moodboard": "claude-sonnet-5",
    "report": "claude-sonnet-5",
}


def step_routing(ctx: Ctx) -> ExecutionPlan:
    track = Track.DATA_DRIVEN if ctx.analysis.footfall_upload_id else Track.SIMULATED
    steps = [s for s in STEP_NAMES if s not in ("intake", "routing")]
    return ExecutionPlan(
        track=track,
        steps=steps,
        total_budget_usd=costs.DEFAULT_BUDGET_USD,
        budgets=[
            StepBudget(
                step=s,
                model=STEP_MODELS.get(s, "deterministic"),
                max_usd=costs.STEP_MAX_USD.get(s, costs.DEFAULT_STEP_MAX_USD),
            )
            for s in steps
        ],
    )


def step_zones(ctx: Ctx) -> ZoneGraph:
    if settings.agents_enabled():
        from . import agents

        plan_bytes = ctx.upload_bytes(ctx.analysis.floorplan_upload_id)
        if plan_bytes is not None:
            # A sheet carrying several floor plates is normal architectural practice and
            # this product analyses one plate, so say which reading the client is getting
            # rather than letting them assume the whole sheet was covered.
            floors = (ctx.outputs.get("intake") or {}).get("floors_detected") or 1
            if isinstance(floors, int) and floors > 1:
                _emit(ctx.session, ctx.analysis.id, "step",
                      f"zones: the sheet shows {floors} floor plates — analysing the "
                      "ground/entrance plate only; upload one plate per analysis to "
                      "cover the others")
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
            report.notes.append("The uploaded plan could not be rendered as an image, so no heatmap was produced.")
    return report


def step_layout(ctx: Ctx) -> LayoutProposals:
    objectives = [Objective(o) for o in ctx.analysis.objectives] or [Objective.GUEST_FLOW]
    if settings.agents_enabled():
        from . import agents

        graph = ZoneGraph.model_validate(ctx.outputs["zones"])
        proposals = agents.run_layout(
            graph,
            FlowReport.model_validate(ctx.outputs["flow"]),
            objectives,
            ctx.analysis.project.space_type,
            ctx.analysis.brief,
        )
        # The model proposes, CP-SAT decides (docs/01 — "solver guarantees").
        from . import solver

        checked = solver.validate(proposals, graph)
        infeasible = [s.name for s in checked.scenarios if not s.solver_feasible]
        if infeasible:
            _emit(ctx.session, ctx.analysis.id, "step",
                  f"layout: {len(infeasible)}/{len(checked.scenarios)} scenarios flagged "
                  f"infeasible by the constraint solver ({', '.join(infeasible)[:80]})")
        return checked
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
        from . import imagegen

        keys, errors, renders = agents.generate_moodboard_images(
            board.style_name, board.materials, ctx.analysis.project.space_type, graph
        )
        providers = sorted({r.provider for r in renders})
        # Draft if ANY render came from the fallback: a report cannot caption half its
        # images as final and half as draft, so the weaker claim governs the set.
        draft = any(r.is_draft for r in renders)
        if errors or renders:
            # Always visible, never fatal: renders are an enhancement over
            # palette/materials, and which provider served them is operator info.
            via = f" via {', '.join(providers)}" if providers else ""
            size = imagegen.describe_resolution(renders)
            quality = " — DRAFT quality, captioned as such in the report" if draft else ""
            # generate_render raises one joined string per failed render listing EVERY
            # provider it tried. Truncating that to 80 chars showed only the first
            # provider, hiding the line that actually tells the operator what to do —
            # "flux: 402 you have depleted your monthly included credits".
            reason = ""
            if errors:
                parts = [p.strip()[:90] for p in errors[0].split(";") if p.strip()]
                reason = " (" + " | ".join(parts) + ")"
            _emit(
                ctx.session,
                ctx.analysis.id,
                "step",
                f"moodboard: {len(keys)}/3 renders generated{via}{size}{reason}{quality} — continuing",
            )
        return board.model_copy(update={
            "image_keys": keys,
            "render_provider": ", ".join(providers) or None,
            "renders_are_draft": draft,
        })
    return Moodboard(
        style_name="Serene boutique",
        palette=["#FBFAF7", "#1C4A3E", "#B08D57"],
        materials=["travertine", "walnut", "linen"],
    )


def flow_efficiency_score(graph: ZoneGraph, flow: FlowReport) -> float | None:
    """Area-weighted mean flow intensity across guest-facing zones, on 0-100.
    Deterministic math — the model never invents this number.

    Intensities arrive normalised against the busiest zone on the whole plan, which
    is what the heatmap needs: a packed kitchen really is the hottest room. But the
    score covers only guest-facing zones, and mixing the two made the headline number
    swing ~19 points when a single excluded zone was re-typed, with every intensity
    identical. So the score re-normalises inside the set it actually scores: it reads
    "how evenly is the guest space used, relative to its own busiest room", and the
    assumption below says exactly that.
    """
    from .geometry import polygon_area

    intensity = {f.zone_id: f.intensity for f in flow.zone_flows}
    scored = [z for z in graph.zones if z.category not in SCORE_EXCLUDED]
    if not scored:
        return None  # nothing guest-facing on this plan; a note explains it
    peak = max((intensity.get(z.id, 0.0) for z in scored), default=0.0)
    if peak <= 0:
        return 0.0  # guest space with no traffic at all is a finding, not a gap

    weighted = total = 0.0
    for zone in scored:
        area = polygon_area(zone.polygon)
        weighted += area * (intensity.get(zone.id, 0.0) / peak)
        total += area
    if total == 0:
        return None
    return round(100 * weighted / total, 1)


def step_business(ctx: Ctx) -> BusinessCase:
    graph = ZoneGraph.model_validate(ctx.outputs["zones"])
    flow = FlowReport.model_validate(ctx.outputs["flow"])
    score = flow_efficiency_score(graph, flow)
    excluded = ", ".join(sorted(c.value for c in SCORE_EXCLUDED))
    assumptions = [
        Assumption(
            statement="Flow Efficiency Score = area-weighted mean flow intensity across "
            "guest-facing zones, measured relative to the busiest guest-facing zone and "
            f"scaled to 0-100. Excluded zone types: {excluded}.",
            source="deterministic",
        ),
        Assumption(
            # Taken from the flow step's own note rather than restated here. This
            # assumption used to hardcode "distance decay from entrances", which stopped
            # being true when JuPedSim became the simulated track — so a client reading
            # their report was told the wrong method, and distance decay is now only the
            # fallback the note names explicitly when it is used.
            statement=(
                "Intensities are measured from uploaded footfall data."
                if flow.track == Track.DATA_DRIVEN
                else (flow.notes[0] if flow.notes else "Intensities are simulated.")
                + " Upload footfall data for measured values."
            ),
            source="pipeline",
        ),
        Assumption(
            statement="Revenue-per-sqm projections require venue revenue baselines "
            "(pending founder presets per space type) — not yet computed.",
            source="preset",
        ),
    ]
    if score is None:
        # A hotel guest floor reaches here legitimately: guest rooms are excluded from
        # the score by design, so a floor that is only rooms and a corridor can leave
        # nothing to measure. Telling that client "no guest-facing zone" would be plainly
        # false — they are looking at a floor full of guest rooms — so the note names the
        # reason it actually applies.
        rooms = sum(1 for z in graph.zones if z.category is ZoneCategory.GUESTROOM)
        reason = (
            f"every scored zone is excluded — this plate is {rooms} guest "
            "room(s) plus circulation, and guest rooms are private destinations that "
            "do not evidence circulation quality"
            if rooms
            else "every zone is back-of-house or utility"
        )
        _emit(
            ctx.session,
            ctx.analysis.id,
            "step",
            f"Flow Efficiency Score not computed: {reason}.",
        )
    return BusinessCase(flow_efficiency_score=score, assumptions=assumptions)


def step_report(ctx: Ctx) -> ReportArtifact:
    graph = ZoneGraph.model_validate(ctx.outputs["zones"])
    flow = FlowReport.model_validate(ctx.outputs["flow"])
    layout = LayoutProposals.model_validate(ctx.outputs["layout"])
    moodboard = Moodboard.model_validate(ctx.outputs["moodboard"])
    business = ctx.outputs["business"]
    project = ctx.analysis.project

    language = ctx.analysis.report_language or "en"
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
            language,
        )
        narrative = wire.model_dump()
    else:
        # Stub path (tests/no key): no narrative, no PDF — report_key stays None.
        return ReportArtifact(language="en", sections=["summary", "zones", "flow", "layout", "design"])

    from . import pdf as pdf_mod
    from . import report_html

    # Tolerant loads (review F2): a vanished artifact file must degrade the report,
    # not fail it — flow/moodboard steps are 'done' and resume would re-fail forever.
    def optional_load(key: str | None) -> bytes | None:
        if not key:
            return None
        try:
            return storage.load(key)
        except (FileNotFoundError, ValueError):
            _emit(ctx.session, ctx.analysis.id, "step",
                  f"report: stored artifact {key[:12]}… missing — continuing without it")
            return None

    heatmap_png = optional_load(flow.heatmap_key)
    moodboard_pngs = [png for k in moodboard.image_keys[:3] if (png := optional_load(k))]
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
        language=language,
    )
    key = storage.save(pdf_mod.html_to_pdf(html), ".pdf")
    return ReportArtifact(
        report_key=key,
        language=language,
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


def _budget_for(ctx: Ctx) -> float:
    """The analysis ceiling: the routing plan's figure once it exists, else the default
    (intake and routing run before any plan is written)."""
    plan = ctx.outputs.get("routing")
    if isinstance(plan, dict) and isinstance(plan.get("total_budget_usd"), (int, float)):
        # min, not the plan's word: a stored plan from before the ceiling was configured
        # (or one asking for more) must never raise the operator's limit.
        return min(float(plan["total_budget_usd"]), costs.DEFAULT_BUDGET_USD)
    return costs.DEFAULT_BUDGET_USD


def _prior_spend_usd(session: Session, analysis_id: str) -> float:
    """What earlier runs of this analysis already spent, so a resume continues against
    the same ceiling instead of being handed a fresh one."""
    rows = session.query(CostEntry.usd).filter(CostEntry.analysis_id == analysis_id).all()
    return round(sum(r[0] or 0.0 for r in rows), 6)


def _persist_costs(session: Session, analysis_id: str) -> None:
    """Flush new ledger rows to the DB so a crashed run still has its receipt.

    Driven by what the ledger has not yet flushed, never by the DB row count: comparing
    against existing rows dropped every entry of a resumed run, because the prior run's
    rows made the offset larger than the new in-memory ledger.
    """
    pending = costs.unflushed()
    for entry in pending:
        session.add(CostEntry(analysis_id=analysis_id, **entry))
    session.commit()
    costs.mark_flushed(len(pending))


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
    ledger = costs.start_ledger(_prior_spend_usd(session, analysis.id))
    try:
        for step in analysis.steps:
            if step.status == "done" and step.output is not None:
                ctx.outputs[step.name] = step.output
            else:
                # Before, not after: the point is to not start work that cannot be paid
                # for. Checking afterwards produces a receipt, not a guard.
                costs.set_budget(_budget_for(ctx))
                costs.check_budget(step.name, costs.current_budget_usd())
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
                _persist_costs(session, analysis.id)
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

        _emit(session, analysis.id, "pipeline", f"Model spend: {costs.summary()}")
        qa = QAVerdict.model_validate(ctx.outputs["qa"])
        analysis.status = "done" if qa.passed else "failed"
        if not qa.passed:
            analysis.error = "; ".join(i.description for i in qa.issues)
        session.commit()
        _emit(session, analysis.id, "pipeline", f"Analysis {analysis.status}")
    except costs.BudgetExceeded as exc:
        # Not a crash: the guard did its job. The steps that already ran keep their
        # output, the rest are marked skipped rather than failed, and the receipt says
        # exactly where the money went.
        for step in analysis.steps:
            if step.status in ("pending", "running"):
                step.status = "skipped"
                step.error = str(exc)
        analysis.status = "failed"
        analysis.error = str(exc)
        _persist_costs(session, analysis.id)
        session.commit()
        _emit(session, analysis.id, "pipeline", f"Budget ceiling hit — {costs.summary()}")
    except Exception as exc:  # noqa: BLE001 — step failures must land in the DB, not the void
        for step in analysis.steps:
            if step.status == "running":
                step.status = "failed"
                step.error = str(exc)
        analysis.status = "failed"
        analysis.error = str(exc)
        _persist_costs(session, analysis.id)
        session.commit()
        _emit(session, analysis.id, "pipeline", f"Analysis failed: {exc}")
    finally:
        # Always release the ledger: a leaked ContextVar would bill the next analysis
        # that reuses this worker thread for tokens it never spent.
        costs.stop_ledger(ledger)
