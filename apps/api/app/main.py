import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meyraki_contracts import CONTRACT_VERSION, Objective, SpaceType, json_schemas

from . import auth as auth_mod
from . import footfall, ratelimit, settings, storage
from .db import SessionLocal, get_session, init_db
from .models import Analysis, Event, Org, Project, StepRun, Upload, User
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


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


@app.post("/auth/register", status_code=201)
def register(body: RegisterIn, response: Response, session: Session = Depends(get_session)) -> dict:
    email = body.email.strip().lower()
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
def health() -> dict:
    return {"status": "ok", "contracts": CONTRACT_VERSION}


@app.get("/contracts")
def contracts() -> dict:
    return json_schemas()


# ---------------------------------------------------------------- projects

class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    client_name: str | None = None
    space_type: SpaceType = SpaceType.OTHER


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
        if not data.startswith(settings.FLOORPLAN_MAGIC):
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

    analysis = Analysis(
        project_id=project_id,
        objectives=[o.value for o in body.objectives],
        brief=body.brief,
        report_language=body.report_language,
        floorplan_upload_id=body.floorplan_upload_id,
        footfall_upload_id=body.footfall_upload_id,
    )
    session.add(analysis)
    session.flush()
    for i, name in enumerate(STEP_NAMES):
        session.add(StepRun(analysis_id=analysis.id, position=i, name=name))
    session.commit()

    tasks.add_task(_run_in_background, analysis.id)
    return {"id": analysis.id, "status": analysis.status}


@app.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth_mod.current_user),
) -> dict:
    analysis = _own_analysis(session, user, analysis_id)
    return {
        "id": analysis.id,
        "project_id": analysis.project_id,
        "status": analysis.status,
        "objectives": analysis.objectives,
        "error": analysis.error,
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


@app.get("/analyses/{analysis_id}/report.pdf")
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
        headers={"Content-Disposition": f'inline; filename="meyraki-insight-{analysis_id[:8]}.pdf"'},
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
    return Response(content=data, media_type=media)


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
