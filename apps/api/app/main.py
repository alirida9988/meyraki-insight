import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meyraki_contracts import CONTRACT_VERSION, Objective, SpaceType, json_schemas

from . import auth as auth_mod
from . import sharing
from . import billing, footfall, imaging, ratelimit, settings, storage, version
from .db import SessionLocal, get_session, init_db
from .models import Analysis, CostEntry, Event, Org, Project, StepRun, Upload, User
from .pipeline import STEP_NAMES, run_analysis


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Meyraki Insight API", version="0.1.0", lifespan=lifespan)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def guard(request, call_next):
    """Rate limiting + server-side CSRF origin check.

    Registered BEFORE CORSMiddleware so CORS wraps it: every response this
    returns (including 429) still carries CORS headers and is readable by the
    browser (review M4).
    """
    from fastapi.responses import JSONResponse

    # Multipart uploads are CORS-safelisted (no preflight), so SameSite is not
    # the only thing standing between us and cross-site writes (review m11).
    origin = request.headers.get("origin")
    if request.method in UNSAFE_METHODS and origin and origin not in settings.WEB_ORIGINS:
        return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
    try:
        ratelimit.check(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=429, headers=exc.headers)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.WEB_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- auth

class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=auth_mod.MIN_PASSWORD_LEN, max_length=200)
    org_name: str = Field(min_length=1, max_length=200)

    @field_validator("email", mode="before")
    @classmethod
    def strip_email(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("org_name")
    @classmethod
    def require_org_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Organization name cannot be blank.")
        return value


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


@app.post("/auth/register", status_code=201)
def register(body: RegisterIn, response: Response, session: Session = Depends(get_session)) -> dict:
    email = body.email.strip().lower()
    # A deployment reachable from the internet is a deployment strangers can sign up to,
    # and every analysis they run spends the owner's model credits. Setting
    # MEYRAKI_ALLOWED_EMAILS closes registration to a named list; leaving it unset keeps
    # signup open, which is right for development and wrong the moment a URL is shared.
    # Only registration is gated — existing accounts keep working, so adding the list
    # later never locks anyone out of their own data.
    if not settings.registration_allowed(email):
        raise HTTPException(
            403,
            "This deployment is invitation-only. Ask the owner to add your email address.",
        )
    if session.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(409, "An account with this email already exists — sign in instead.")
    org = Org(name=body.org_name.strip())
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email=email, password_hash=auth_mod.hash_password(body.password))
    session.add(user)
    try:
        session.commit()
    except IntegrityError:  # concurrent register of the same email (review m14)
        session.rollback()
        raise HTTPException(409, "An account with this email already exists — sign in instead.")
    auth_mod.set_cookie(response, auth_mod.create_session(session, user))
    return {"email": user.email, "org": org.name}


@app.post("/auth/login")
def login(body: LoginIn, response: Response, session: Session = Depends(get_session)) -> dict:
    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    # Always pay the bcrypt cost so response time can't reveal whether the
    # account exists (review M2).
    ok = auth_mod.verify_password(
        body.password, user.password_hash if user else auth_mod.DUMMY_HASH
    )
    if user is None or not ok:
        raise HTTPException(401, "Email or password is incorrect.")
    auth_mod.set_cookie(response, auth_mod.create_session(session, user))
    org = session.get(Org, user.org_id)
    return {"email": user.email, "org": org.name if org else ""}


@app.post("/auth/logout")
def logout(request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    token = request.cookies.get(auth_mod.COOKIE)
    if token:
        auth_mod.destroy_session(session, token)
    response.delete_cookie(auth_mod.COOKIE)
    return {"ok": True}


@app.get("/auth/me")
def me(user: User = Depends(auth_mod.current_user), session: Session = Depends(get_session)) -> dict:
    org = session.get(Org, user.org_id)
    return {"email": user.email, "org": org.name if org else ""}


@app.get("/health")
def health(response: Response, session: Session = Depends(get_session)) -> dict:
    """Liveness AND the one dependency without which nothing works.

    This used to return ok without touching the database. With Postgres stopped, Docker
    reported the container healthy, `restart: unless-stopped` never fired, and the web
    service's `depends_on: service_healthy` gated on that — while every real endpoint
    returned 500. A health check that cannot fail is decoration.
    """
    try:
        session.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001 — the reason belongs in the response
        response.status_code = 503
        return {"status": "unavailable", "database": str(exc)[:120],
                "contracts": CONTRACT_VERSION, "build": version.build_version()}
    # `build` is here so a deployed instance can be identified without shell access:
    # "which commit is actually serving this?" is the first question of any incident.
    return {"status": "ok", "contracts": CONTRACT_VERSION, "build": version.build_version()}


@app.get("/billing")
def billing_state(
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    """What this organisation is entitled to, and why.

    Exposed so the interface can say "2 of 3 included analyses left" before a studio
    uploads a plan, rather than letting them do the work and meet a 402 at the end. The
    same evaluation the analysis endpoint uses, so the two can never disagree.
    """
    org = session.get(Org, user.org_id)
    used = session.scalar(
        select(func.count(Analysis.id))
        .join(Project, Project.id == Analysis.project_id)
        .where(Project.org_id == user.org_id)
    ) or 0
    e = billing.evaluate(
        plan=org.plan if org else None,
        subscription_status=org.subscription_status if org else None,
        credits=org.analysis_credits if org else 0,
        analyses_used=int(used),
    )
    return {
        "enforced": billing.enabled(),
        "plan": e.plan,
        "subscription_status": e.subscription_status,
        "analyses_used": e.used,
        "analyses_included": e.included,
        "analyses_remaining": e.remaining_free,
        "credits": e.credits,
        "can_start_analysis": e.allowed,
        "reason": e.reason,
    }


@app.get("/contracts")
def contracts() -> dict:
    return json_schemas()


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client_name: str | None = None
    space_type: SpaceType = SpaceType.OTHER

    @field_validator("name")
    @classmethod
    def require_project_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank.")
        return value

    @field_validator("client_name")
    @classmethod
    def normalize_client_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


@app.post("/projects", status_code=201)
def create_project(
    body: ProjectIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    project = Project(org_id=user.org_id, name=body.name, client_name=body.client_name, space_type=body.space_type)
    session.add(project)
    session.commit()
    return _project_out(project)


@app.get("/projects")
def list_projects(
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> list[dict]:
    projects = session.scalars(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.created_at.desc())
    ).all()
    return [_project_out(p) for p in projects]


def _own_project(session: Session, user: User, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(404, "Project not found")
    return project


def _own_analysis(session: Session, user: User, analysis_id: str) -> Analysis:
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(404, "Analysis not found")
    project = session.get(Project, analysis.project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(404, "Analysis not found")
    return analysis


def _project_out(p: Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "client_name": p.client_name,
        "space_type": p.space_type,
        "created_at": p.created_at.isoformat(),
    }


# ---------------------------------------------------------------- uploads

@app.post("/projects/{project_id}/uploads", status_code=201)
async def upload_file(
    project_id: str,
    file: UploadFile,
    kind: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    _own_project(session, user, project_id)
    if kind not in ("floorplan", "footfall"):
        raise HTTPException(422, 'kind must be "floorplan" or "footfall"')

    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File exceeds 25 MB — export a smaller version.")
    if not data:
        raise HTTPException(422, "File is empty.")

    meta: dict = {}
    if kind == "floorplan":
        if file.content_type not in settings.FLOORPLAN_TYPES:
            raise HTTPException(
                422, "Floorplan must be PNG, JPG, or PDF. DWG support arrives in Phase 2."
            )
        # Trust boundary: verify contents, never the client-supplied content type.
        if not data.startswith(settings.FLOORPLAN_MAGIC) or not imaging.valid_floorplan(data):
            raise HTTPException(
                422, "File contents are not a valid PNG, JPG, or PDF — re-export and try again."
            )
    else:
        errors, rows = footfall.validate(data)
        if errors:
            raise HTTPException(422, detail={"errors": errors})
        meta["rows"] = rows

    # Display name only — storage uses a uuid key; DB column is varchar(300).
    filename = (file.filename or "upload")[:200]
    key = storage.save(data, Path(filename).suffix.lower()[:16] or ".bin")
    upload = Upload(
        project_id=project_id,
        kind=kind,
        filename=filename,
        content_type=(file.content_type or "application/octet-stream")[:100],
        storage_key=key,
        size_bytes=len(data),
        meta=meta,
    )
    session.add(upload)
    try:
        session.commit()
    except Exception:
        session.rollback()
        storage.delete(key)  # never leave an orphaned file behind a failed DB write
        raise HTTPException(422, "Upload could not be saved — check the file and try again.")
    return {"id": upload.id, "kind": kind, "filename": upload.filename, "meta": meta}


# ---------------------------------------------------------------- analyses

class AnalysisIn(BaseModel):
    floorplan_upload_id: str
    footfall_upload_id: str | None = None
    objectives: list[Objective] = Field(min_length=1)
    brief: str | None = Field(None, max_length=2000)
    report_language: Literal["en", "ar"] = "en"


def _analysis_fingerprint(project_id: str, body: AnalysisIn) -> str:
    """Canonical request identity used to make an in-flight analysis idempotent."""
    payload = {
        "project_id": project_id,
        "floorplan_upload_id": body.floorplan_upload_id,
        "footfall_upload_id": body.footfall_upload_id,
        "objectives": sorted(objective.value for objective in body.objectives),
        "brief": body.brief,
        "report_language": body.report_language,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _run_in_background(analysis_id: str) -> None:
    # ponytail: in-process execution; becomes a worker pool when the real agents land (M2)
    with SessionLocal() as session:
        run_analysis(session, analysis_id)


@app.post("/projects/{project_id}/analyses", status_code=201)
def start_analysis(
    project_id: str,
    body: AnalysisIn,
    tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    _own_project(session, user, project_id)
    plan = session.get(Upload, body.floorplan_upload_id)
    if plan is None or plan.project_id != project_id or plan.kind != "floorplan":
        raise HTTPException(
            422, "That floorplan doesn't belong to this project — upload it here first."
        )
    if body.footfall_upload_id:
        ff = session.get(Upload, body.footfall_upload_id)
        if ff is None or ff.project_id != project_id or ff.kind != "footfall":
            raise HTTPException(
                422, "That footfall file doesn't belong to this project — upload it here first."
            )

    fingerprint = _analysis_fingerprint(project_id, body)
    # Fast path for a repeat request.  The unique partial index below is still the
    # authority: this read cannot make two concurrent requests safe on its own.
    active = session.scalar(
        select(Analysis)
        .where(
            Analysis.project_id == project_id,
            Analysis.request_fingerprint == fingerprint,
            Analysis.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    if active is not None:
        return {"id": active.id, "status": active.status}

    # Checked here rather than at the top of the handler so a retry of an already-running
    # analysis is neither refused nor billed twice — the fast path above returns first.
    org = session.get(Org, user.org_id)
    used = session.scalar(
        select(func.count(Analysis.id))
        .join(Project, Project.id == Analysis.project_id)
        .where(Project.org_id == user.org_id)
    ) or 0
    entitlement = billing.evaluate(
        plan=org.plan if org else None,
        subscription_status=org.subscription_status if org else None,
        credits=org.analysis_credits if org else 0,
        analyses_used=int(used),
    )
    if not entitlement.allowed:
        # 402 rather than 403: this is not a permission the studio lacks, it is a payment
        # the account has not made, and the message says which and what to do next.
        raise HTTPException(402, entitlement.reason)

    analysis = Analysis(
        project_id=project_id,
        objectives=[o.value for o in body.objectives],
        brief=body.brief,
        report_language=body.report_language,
        floorplan_upload_id=body.floorplan_upload_id,
        footfall_upload_id=body.footfall_upload_id,
        request_fingerprint=fingerprint,
        app_version=version.build_version(),
    )
    session.add(analysis)
    session.flush()
    for i, name in enumerate(STEP_NAMES):
        session.add(StepRun(analysis_id=analysis.id, position=i, name=name))
    if billing.consumes_credit(entitlement) and org is not None:
        # Inside the same transaction as the insert, deliberately. If the commit below
        # loses the duplicate-request race, the rollback takes the credit back with it —
        # a studio charged for an analysis that was never created is the one billing bug
        # they would not forgive, and the race is real enough to have its own index.
        org.analysis_credits = max(0, org.analysis_credits - 1)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent request won the partial unique index.  Return that run rather
        # than launching another model bill; only hide the collision when it is the
        # exact active request we were trying to create.
        session.rollback()
        active = session.scalar(
            select(Analysis)
            .where(
                Analysis.project_id == project_id,
                Analysis.request_fingerprint == fingerprint,
                Analysis.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        if active is not None:
            return {"id": active.id, "status": active.status}
        raise

    tasks.add_task(_run_in_background, analysis.id)
    return {"id": analysis.id, "status": analysis.status}


@app.get("/projects/{project_id}/analyses")
def list_analyses(
    project_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> list[dict]:
    """Analysis history for a project — what a user comes back to after a refresh."""
    _own_project(session, user, project_id)
    rows = session.scalars(
        select(Analysis)
        .where(Analysis.project_id == project_id)
        .order_by(Analysis.created_at.desc())
        .limit(50)
    ).all()
    return [
        {
            "id": a.id,
            "status": a.status,
            "objectives": a.objectives,
            "report_language": a.report_language,
            "created_at": a.created_at.isoformat(),
            "error": a.error,
        }
        for a in rows
    ]


@app.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    analysis = _own_analysis(session, user, analysis_id)
    # What this analysis actually spent on models, org-scoped like everything else.
    # An unenforced budget was fiction until M5; an unreported one is only half a fix.
    entries = (
        session.query(CostEntry).filter(CostEntry.analysis_id == analysis.id).all()
    )
    return {
        "id": analysis.id,
        "project_id": analysis.project_id,
        "status": analysis.status,
        "objectives": analysis.objectives,
        "error": analysis.error,
        "cost": {
            "usd": round(sum(e.usd for e in entries), 6),
            "by_step": [
                {"step": e.step, "model": e.model, "input_tokens": e.input_tokens,
                 "output_tokens": e.output_tokens, "usd": round(e.usd, 6)}
                for e in entries
            ],
        },
        "steps": [
            {"name": s.name, "status": s.status, "output": s.output, "error": s.error}
            for s in analysis.steps
        ],
    }


@app.post("/analyses/{analysis_id}/resume")
def resume_analysis(
    analysis_id: str,
    tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    _own_analysis(session, user, analysis_id)
    # Atomic claim: exactly one concurrent caller wins (rowcount == 1); a 'running'
    # analysis is claimable only when its heartbeat is stale (crashed worker).
    from sqlalchemy import update

    from .pipeline import claimable_where

    now = datetime.now(timezone.utc)
    claimed = session.execute(
        update(Analysis)
        .where(*claimable_where(analysis_id, ("failed", "queued"), now))
        .values(status="queued", error=None, heartbeat_at=None)
    ).rowcount
    session.commit()
    if claimed != 1:
        session.expire_all()
        analysis = session.get(Analysis, analysis_id)
        if analysis is None:
            raise HTTPException(404, "Analysis not found")
        raise HTTPException(409, f"Analysis is {analysis.status} — nothing to resume")

    session.expire_all()
    analysis = session.get(Analysis, analysis_id)
    for s in analysis.steps:
        if s.status in ("running", "failed"):
            s.status = "pending"
            s.error = None
    session.commit()
    tasks.add_task(_run_in_background, analysis.id)
    return {"id": analysis.id, "status": "queued"}


@app.post("/analyses/{analysis_id}/share")
def share_report(
    analysis_id: str,
    request: Request,
    ttl_days: int = 7,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    """Mint an expiring link a studio can send to a client who has no account.

    Org-scoped like everything else: you can only share an analysis you own. The link
    itself carries no session and grants nothing beyond this one report.
    """
    analysis = _own_analysis(session, user, analysis_id)
    report_step = next((s for s in analysis.steps if s.name == "report"), None)
    if not (report_step and (report_step.output or {}).get("report_key")):
        raise HTTPException(404, "Report not ready for this analysis")
    # The QA Verifier runs after the report is written, so a report file exists even for
    # an analysis whose layout references zones the Zone Analyst never produced. Marking
    # the analysis "failed" was never enough on its own: this is the endpoint that puts a
    # document in a client's hands, so this is where the gate has to bite.
    if analysis.status != "done":
        raise HTTPException(
            409,
            "This analysis did not pass QA, so it cannot be shared with a client. "
            f"Status: {analysis.status}."
            + (f" {analysis.error}" if analysis.error else ""),
        )
    try:
        signature, expires_at = sharing.mint(analysis_id, ttl_days * 24 * 3600)
    except sharing.SharingDisabled as exc:
        raise HTTPException(503, str(exc)) from exc
    path = f"/shared/reports/{analysis_id}?expires={expires_at}&sig={signature}"
    return {"url": str(request.base_url).rstrip("/") + path, "expires_at": expires_at}


# Every response below carries client data — a floorplan, a heatmap, a priced report.
# Putting a CDN in front of the API turned that into a leak: Cloudflare treats a URL
# ending in .pdf as a static asset, cached it for four hours, and served it to anyone
# who asked, while the origin itself correctly answered 401. The origin was right and
# still the document got out, because nothing told the edge the bytes were private.
#
# `private` forbids shared caches, `no-store` forbids writing it down at all, and
# `max-age=0` covers intermediaries that honour only the older directive.
NO_STORE = {
    "Cache-Control": "private, no-store, max-age=0, must-revalidate",
    "Pragma": "no-cache",
}


@app.get("/shared/reports/{analysis_id}")
def shared_report(
    analysis_id: str,
    expires: int = 0,
    sig: str = "",
    session: Session = Depends(get_session),
) -> Response:
    """Serve a report to whoever holds a valid link. No session, by design.

    Every failure returns the same 404 as an unknown analysis: an expired link, a forged
    signature and a nonexistent id must be indistinguishable, or the endpoint becomes an
    oracle for which analyses exist.
    """
    if not sharing.verify(analysis_id, expires, sig):
        raise HTTPException(404, "This link is not valid or has expired")
    analysis = session.get(Analysis, analysis_id)
    # Re-checked here and not only at minting: an analysis that was clean when the link
    # was sent can be re-run and fail, and a link already sitting in a client's inbox
    # must go dead rather than keep serving the superseded document. Same 404 as every
    # other failure, so the endpoint still tells an outsider nothing.
    if analysis is None or analysis.status != "done":
        raise HTTPException(404, "This link is not valid or has expired")
    report_step = next((s for s in analysis.steps if s.name == "report"), None)
    key = (report_step.output or {}).get("report_key") if report_step else None
    if not key:
        raise HTTPException(404, "This link is not valid or has expired")
    try:
        content = storage.load(key)
    except FileNotFoundError as exc:
        raise HTTPException(404, "This link is not valid or has expired") from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={**NO_STORE,
                 "Content-Disposition": f'inline; filename="meyraki-report-{analysis_id[:8]}.pdf"'},
    )


# Deliberately NOT ".pdf". A CDN's default rules cache by file extension, and this route
# returns one client's priced report: Cloudflare cached it for four hours and served it to
# anonymous callers while the origin correctly answered 401. The Cache-Control headers
# below are the real fix, but an extension that invites a static-asset heuristic is a trap
# to walk around rather than to keep defusing.
@app.get("/analyses/{analysis_id}/report")
def download_report(
    analysis_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> Response:
    analysis = _own_analysis(session, user, analysis_id)
    report_step = next((s for s in analysis.steps if s.name == "report"), None)
    key = (report_step.output or {}).get("report_key") if report_step else None
    if not key:
        raise HTTPException(404, "Report not ready for this analysis")
    try:
        content = storage.load(key)
    except (ValueError, OSError):
        raise HTTPException(404, "Report file no longer available — re-run the analysis")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={**NO_STORE,
                 "Content-Disposition": f'inline; filename="meyraki-insight-{analysis_id[:8]}.pdf"'},
    )


def _artifact_keys(output: dict | None) -> set[str]:
    """Storage keys an analysis legitimately exposes (heatmap, renders, report)."""
    if not isinstance(output, dict):
        return set()
    keys = {
        value
        for field in ("heatmap_key", "report_key")
        if isinstance(value := output.get(field), str) and value
    }
    keys |= {k for k in (output.get("image_keys") or []) if isinstance(k, str) and k}
    return keys


@app.get("/analyses/{analysis_id}/files/{key}")
def get_file(
    analysis_id: str,
    key: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> Response:
    """Serve stored artifacts, scoped to the analysis that owns them.

    Artifact keys travel in API responses and <img> URLs, so authentication
    alone is not a boundary — the key must belong to an analysis of the
    caller's org (review B1).
    """
    analysis = _own_analysis(session, user, analysis_id)
    allowed: set[str] = set()
    for step in analysis.steps:
        allowed |= _artifact_keys(step.output)
    if key not in allowed:
        raise HTTPException(404, "File not found")
    try:
        data = storage.load(key)
    except (ValueError, OSError):  # OSError covers missing + IsADirectory (review m13)
        raise HTTPException(404, "File not found")
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".pdf": "application/pdf",
    }.get(Path(key).suffix.lower(), "application/octet-stream")
    # Heatmaps and renders are as private as the report they illustrate, and this route
    # serves .png and .pdf — the extensions a CDN caches most eagerly.
    return Response(content=data, media_type=media, headers=NO_STORE)


@app.get("/analyses/{analysis_id}/events")
async def stream_events(
    analysis_id: str,
    session: Session = Depends(get_session),
    last_event_id: str | None = Header(None),
    user: User = Depends(auth_mod.current_user),
) -> StreamingResponse:
    """SSE progress stream — polls the events table; fine for dev scale."""
    _own_analysis(session, user, analysis_id)

    async def gen():
        # Resume from Last-Event-ID so browser reconnects don't replay the log.
        last_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        for _ in range(600):  # hard stop after ~5 min
            with SessionLocal() as s:
                events = s.scalars(
                    select(Event)
                    .where(Event.analysis_id == analysis_id, Event.id > last_id)
                    .order_by(Event.id)
                ).all()
                analysis = s.get(Analysis, analysis_id)
                for e in events:
                    last_id = e.id
                    payload = json.dumps({"kind": e.kind, "message": e.message})
                    yield f"id: {e.id}\ndata: {payload}\n\n"
                if analysis is None:
                    yield f'data: {json.dumps({"kind": "end", "message": "gone"})}\n\n'
                    return
                if analysis.status in ("done", "failed", "rejected"):
                    yield f'data: {json.dumps({"kind": "end", "message": analysis.status})}\n\n'
                    return
            await asyncio.sleep(0.5)
        yield f'data: {json.dumps({"kind": "end", "message": "timeout"})}\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream")
