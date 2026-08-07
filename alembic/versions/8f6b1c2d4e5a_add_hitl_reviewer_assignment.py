"""add HITL reviewer assignment

Revision ID: 8f6b1c2d4e5a
Revises: 2da43d0a4454
Create Date: 2026-08-07

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "8f6b1c2d4e5a"
down_revision: str | Sequence[str] | None = "2da43d0a4454"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add reviewer ownership fields to HITL inspection jobs."""
    op.add_column(
        "return_jobs",
        sa.Column("hitl_reviewer_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "return_jobs",
        sa.Column("hitl_review_started_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_return_jobs_hitl_reviewer_id_users",
        "return_jobs",
        "users",
        ["hitl_reviewer_id"],
        ["id"],
    )
    op.create_index(
        "ix_return_jobs_hitl_reviewer_id",
        "return_jobs",
        ["hitl_reviewer_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove reviewer ownership fields from HITL inspection jobs."""
    op.drop_index(
        "ix_return_jobs_hitl_reviewer_id",
        table_name="return_jobs",
    )
    op.drop_constraint(
        "fk_return_jobs_hitl_reviewer_id_users",
        "return_jobs",
        type_="foreignkey",
    )
    op.drop_column("return_jobs", "hitl_review_started_at")
    op.drop_column("return_jobs", "hitl_reviewer_id")
