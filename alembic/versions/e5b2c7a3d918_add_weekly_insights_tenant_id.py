"""weekly_insights 테넌트 격리

tenant_id를 추가하고 report_week 유일성을 테넌트 단위로 좁힌다.
기존 행은 최초 테넌트로 백필한다.

Revision ID: e5b2c7a3d918
Revises: d7e4a8c19f30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5b2c7a3d918"
down_revision: str | None = "d7e4a8c19f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNIQUE = "weekly_insights_report_week_key"
NEW_UNIQUE = "uq_weekly_insights_tenant_report_week"


def upgrade() -> None:
    # 기존 행이 있으면 바로 NOT NULL을 걸 수 없어 nullable로 추가 후 백필한다.
    op.add_column(
        "weekly_insights",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )

    # 가장 먼저 생성된 테넌트로 백필
    op.execute(
        """
        UPDATE weekly_insights
        SET tenant_id = (
            SELECT id FROM tenants ORDER BY created_at ASC LIMIT 1
        )
        WHERE tenant_id IS NULL
        """
    )

    # 백필할 테넌트가 없는 행은 지표 의미가 없어 제거한다.
    op.execute("DELETE FROM weekly_insights WHERE tenant_id IS NULL")

    op.alter_column("weekly_insights", "tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_weekly_insights_tenant_id",
        "weekly_insights",
        "tenants",
        ["tenant_id"],
        ["id"],
    )
    op.create_index(
        "ix_weekly_insights_tenant_id",
        "weekly_insights",
        ["tenant_id"],
    )

    # report_week 전역 유일 -> (tenant_id, report_week) 복합 유일
    op.drop_constraint(OLD_UNIQUE, "weekly_insights", type_="unique")
    op.create_unique_constraint(
        NEW_UNIQUE,
        "weekly_insights",
        ["tenant_id", "report_week"],
    )


def downgrade() -> None:
    op.drop_constraint(NEW_UNIQUE, "weekly_insights", type_="unique")
    # 전역 유일 제약을 되살리려면 주차별 중복을 먼저 제거해야 한다.
    op.execute(
        """
        DELETE FROM weekly_insights a
        USING weekly_insights b
        WHERE a.report_week = b.report_week
          AND a.created_at > b.created_at
        """
    )
    op.create_unique_constraint(OLD_UNIQUE, "weekly_insights", ["report_week"])

    op.drop_index("ix_weekly_insights_tenant_id", table_name="weekly_insights")
    op.drop_constraint("fk_weekly_insights_tenant_id", "weekly_insights", type_="foreignkey")
    op.drop_column("weekly_insights", "tenant_id")
