import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    client_name: Mapped[str | None] = mapped_column(String(200), default=None)
    space_type: Mapped[str] = mapped_column(String(32), default="other")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    uploads: Mapped[list["Upload"]] = relationship(back_populates="project")
    analyses: Mapped[list["Analysis"]] = relationship(back_populates="project")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(16))  # "floorplan" | "footfall"
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="uploads")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # queued | running | done | failed | rejected
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    brief: Mapped[str | None] = mapped_column(Text, default=None)
    report_language: Mapped[str] = mapped_column(String(2), default="en")  # "en" | "ar"
    floorplan_upload_id: Mapped[str] = mapped_column(String(32))
    footfall_upload_id: Mapped[str | None] = mapped_column(String(32), default=None)
    # A stable hash of the requested inputs.  It lets the database reject a duplicate
    # active run while still allowing a studio to deliberately re-run an analysis later.
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    """Bumped by the runner per step; a 'running' row with a stale heartbeat is a crashed
    run and may be atomically re-claimed (see pipeline.run_analysis / resume)."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    project: Mapped[Project] = relationship(back_populates="analyses")
    steps: Mapped[list["StepRun"]] = relationship(
        back_populates="analysis", order_by="StepRun.position"
    )


# UI disabling is useful feedback, not a data-integrity boundary: retries, double-clicks,
# and another tab can all reach the API.  Enforce one identical queued/running run per
# project in the database so duplicate requests cannot duplicate model spend.
Index(
    "uq_analyses_active_fingerprint",
    Analysis.project_id,
    Analysis.request_fingerprint,
    unique=True,
    sqlite_where=Analysis.status.in_(("queued", "running")),
    postgresql_where=Analysis.status.in_(("queued", "running")),
)


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"))
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending | running | done | failed | skipped
    output: Mapped[dict | None] = mapped_column(JSON, default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    analysis: Mapped[Analysis] = relationship(back_populates="steps")


class CostEntry(Base):
    """What one analysis actually spent, per model call.

    A separate table rather than a column on Analysis: there is no migration tool here
    (`Base.metadata.create_all` creates new tables but never ALTERs an existing one), so
    a new table lands on existing databases and a new column would not.
    """

    __tablename__ = "cost_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    step: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(80))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("analyses.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
