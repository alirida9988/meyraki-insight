from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables on SQLite only. Every other database is owned by Alembic.

    `create_all` on a real deployment is a trap: it makes the tables but writes no
    `alembic_version` row, so the first `alembic upgrade head` afterwards tries to create
    tables that already exist and fails. It also never ALTERs anything, which is how the
    dev database quietly ended up missing an index and a foreign key the models declare.

    SQLite is the test and local path, where a schema is built and thrown away in the
    same second and a migration would only be ceremony. Postgres runs
    `alembic upgrade head` — the API image does it on start.
    """
    from . import models  # noqa: F401  (register tables)

    if engine.dialect.name == "sqlite":
        Base.metadata.create_all(engine)


def get_session():
    with Session(engine, expire_on_commit=False) as session:
        yield session
