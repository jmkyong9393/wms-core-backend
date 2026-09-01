"""weekly_insights 테넌트 격리

weekly_insights만 tenant_id 없이 전역 테이블이라 관리자 대시보드가 다른 고객사의
주간 지표까지 조회하고 있었다. 다른 도메인과 동일하게 테넌트로 격리하고,
report_week 유일성도 전역이 아닌 테넌트 단위로 좁힌다.

기존 행은 삭제하지 않고 최초 테넌트로 백필한다 (단일 테넌트 운영 이력 보존).

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
    # 1. nullable로 추가한 뒤 백필하고 NOT NULL로 조인다.
    #    기존 행이 있는 상태에서 바로 NOT NULL을 걸면 실패한다.
    op.add_column(
        "weekly_insights",
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
    )

    # 2. 가장 먼저 생성된 테넌트로 백필. 단일 테넌트 운영이었으므로 모호성이 없다.
    op.execute(
        """
        UPDATE weekly_insights
        SET tenant_id = (
            SELECT id FROM tenants ORDER BY created_at ASC LIMIT 1
        )
        WHERE tenant_id IS NULL
        """
    )

    # 3. 백필할 테넌트가 없어 남은 행(테넌트가 하나도 없는 빈 DB)은 지표 의미가
    #    없으므로 제거한다.
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

    # 4. report_week 전역 유일 → (tenant_id, report_week) 복합 유일
    op.drop_constraint(OLD_UNIQUE, "weekly_insights", type_="unique")
    op.create_unique_constraint(
        NEW_UNIQUE,
        "weekly_insights",
        ["tenant_id", "report_week"],
    )


def downgrade() -> None:
    op.drop_constraint(NEW_UNIQUE, "weekly_insights", type_="unique")
    # 테넌트별로 같은 주차 행이 여러 개면 전역 유일 제약을 되살릴 수 없다.
    # 되돌리기 전에 중복을 남기고 가장 오래된 행만 보존한다.
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
    op.drop_constraint(
        "fk_weekly_insights_tenant_id", "weekly_insights", type_="foreignkey"
    )
    op.drop_column("weekly_insights", "tenant_id")
