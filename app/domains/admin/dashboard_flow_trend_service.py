from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlmodel import Session, select

from app.domains.admin.schemas.admin_dashboard import (
    DashboardFlowTrendItem,
    DashboardFlowTrendResponse,
)
from app.models.wms import (
    InventoryLog,
    InventoryTransactionType,
    ReturnJob,
)


def get_dashboard_flow_trend(
    session: Session,
    days: int,
) -> DashboardFlowTrendResponse:
    """
    최근 N일의 일별 입고·출고 수량과 완료 검수 건의 평균 처리 시간을 반환한다.

    입고/출고 수량은 InventoryLog를 기준으로 집계한다.
    출고 로그는 음수로 적재되므로 화면에는 절댓값으로 반환한다.
    """
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)
    start_at = datetime.combine(start_date, time.min)

    inventory_logs = session.exec(
        select(InventoryLog).where(
            InventoryLog.created_at >= start_at,
            InventoryLog.transaction_type.in_(
                [
                    InventoryTransactionType.INBOUND,
                    InventoryTransactionType.OUTBOUND,
                ]
            ),
        )
    ).all()

    inbound_by_date: dict = defaultdict(int)
    outbound_by_date: dict = defaultdict(int)

    for inventory_log in inventory_logs:
        log_date = inventory_log.created_at.date()

        if inventory_log.transaction_type == InventoryTransactionType.INBOUND:
            inbound_by_date[log_date] += inventory_log.quantity_change

        elif inventory_log.transaction_type == InventoryTransactionType.OUTBOUND:
            outbound_by_date[log_date] += abs(inventory_log.quantity_change)

    completed_jobs = session.exec(
        select(ReturnJob).where(
            ReturnJob.ai_inspection_completed_at.is_not(None),
            ReturnJob.ai_inspection_completed_at >= start_at,
        )
    ).all()

    inspection_seconds_by_date: dict = defaultdict(list)

    for return_job in completed_jobs:
        completed_at = return_job.ai_inspection_completed_at

        if completed_at is None:
            continue

        processing_seconds = max(
            0.0,
            (completed_at - return_job.ai_inspection_started_at).total_seconds(),
        )

        inspection_seconds_by_date[completed_at.date()].append(processing_seconds)

    items: list[DashboardFlowTrendItem] = []

    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        processing_seconds = inspection_seconds_by_date[current_date]

        items.append(
            DashboardFlowTrendItem(
                date=current_date,
                inbound_quantity=inbound_by_date[current_date],
                outbound_quantity=outbound_by_date[current_date],
                average_inspection_processing_seconds=(
                    sum(processing_seconds) / len(processing_seconds) if processing_seconds else 0.0
                ),
            )
        )

    return DashboardFlowTrendResponse(
        days=days,
        items=items,
    )
