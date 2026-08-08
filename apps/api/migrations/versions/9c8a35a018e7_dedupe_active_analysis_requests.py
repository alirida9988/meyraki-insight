"""Deduplicate identical queued/running analysis requests.

Revision ID: 9c8a35a018e7
Revises: c402d1ed6007
Create Date: 2026-08-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9c8a35a018e7"
down_revision: Union[str, Sequence[str], None] = "c402d1ed6007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_analyses_active_fingerprint",
        "analyses",
        ["project_id", "request_fingerprint"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_analyses_active_fingerprint", table_name="analyses")
    op.drop_column("analyses", "request_fingerprint")
