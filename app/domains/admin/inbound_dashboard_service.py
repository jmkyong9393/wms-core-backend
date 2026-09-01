from collections import defaultdict
from datetime import datetime, time, timedelta

from sqlmodel import Session, select

from app.domains.admin.schemas.admin_dashboard import (
    InboundDashboardGradeItem,
    InboundDashboardSummaryResponse,
    InboundDashboardTrendItem,
    InboundDashboardZoneItem,
    RecentInboundActivityResponse,
)
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    InboundJob,
    Inventory,
    InventoryLog,
    InventoryTransactionType,
    InventoryUsedItem,
    Location,
    ReturnJob,
    ReturnJobStatus,
    UsedInventoryStatus,
)


def get_inbound_dashboard_summary(
    session: Session,
    days: int = 7,
) -> InboundDashboardSummaryResponse:
    """
    입고 현황 탭에서 사용하는 운영 집계 데이터를 반환한다.

    - 실제 입고 수량: inventory_logs의 INBOUND 기준
    - 검수 현황: return_jobs 상태 기준
    - 구역별 가용 재고: 신간 inventory + 판매 가능 중고 LPN 기준
    """
    now = datetime.utcnow()
    today_start = datetime.combine(now.date(), time.min)
    trend_start_date = now.date() - timedelta(days=days - 1)
    trend_start_at = datetime.combine(trend_start_date, time.min)

    inbound_logs = session.exec(
        select(InventoryLog).where(
            InventoryLog.transaction_type == InventoryTransactionType.INBOUND,
            InventoryLog.created_at >= trend_start_at,
        )
    ).all()

    today_inbound_quantity = sum(max(0, log.quantity_change) for log in inbound_logs if log.created_at >= today_start)

    pending_inspection_count = len(
        session.exec(
            select(ReturnJob).where(
                ReturnJob.status.in_(
                    [
                        ReturnJobStatus.PENDING,
                        ReturnJobStatus.PROCESSING,
                        ReturnJobStatus.HITL_REQUIRED,
                    ]
                )
            )
        ).all()
    )

    recheck_required_count = len(
        session.exec(select(ReturnJob).where(ReturnJob.status == ReturnJobStatus.RECHECK_REQUIRED)).all()
    )

    completed_inspection_count = len(
        session.exec(
            select(ReturnJob).where(
                ReturnJob.status.in_(
                    [
                        ReturnJobStatus.APPROVED,
                        ReturnJobStatus.REJECTED,
                    ]
                ),
                ReturnJob.updated_at >= today_start,
            )
        ).all()
    )

    new_stock_by_date: dict = defaultdict(int)
    used_return_by_date: dict = defaultdict(int)

    for log in inbound_logs:
        log_date = log.created_at.date()
        quantity = max(0, log.quantity_change)

        # LPN이 있으면 중고·반품 입고, 없으면 신간 입고로 구분한다.
        if log.target_lpn:
            used_return_by_date[log_date] += quantity
        else:
            new_stock_by_date[log_date] += quantity

    daily_inbound_trend = [
        InboundDashboardTrendItem(
            date=trend_start_date + timedelta(days=offset),
            new_stock_quantity=new_stock_by_date[trend_start_date + timedelta(days=offset)],
            used_return_quantity=used_return_by_date[trend_start_date + timedelta(days=offset)],
        )
        for offset in range(days)
    ]

    completed_return_jobs = session.exec(
        select(ReturnJob).where(
            ReturnJob.status.in_(
                [
                    ReturnJobStatus.APPROVED,
                    ReturnJobStatus.REJECTED,
                ]
            ),
            ReturnJob.condition_grade.is_not(None),
            ReturnJob.updated_at >= trend_start_at,
        )
    ).all()

    grade_counts: dict[ConditionGrade, int] = defaultdict(int)
    for return_job in completed_return_jobs:
        if return_job.condition_grade is not None:
            grade_counts[return_job.condition_grade] += 1

    grade_distribution = [
        InboundDashboardGradeItem(
            grade=grade,
            quantity=grade_counts[grade],
        )
        for grade in ConditionGrade
    ]

    zone_new_stock: dict[str, int] = defaultdict(int)
    zone_used_stock: dict[str, int] = defaultdict(int)

    new_inventory_rows = session.exec(
        select(Inventory, Location).join(
            Location,
            Location.id == Inventory.location_id,
        )
    ).all()

    for inventory, location in new_inventory_rows:
        zone_new_stock[location.zone] += max(
            0,
            inventory.quantity - inventory.reserved_quantity,
        )

    used_inventory_rows = session.exec(
        select(InventoryUsedItem, Location)
        .join(
            Location,
            Location.id == InventoryUsedItem.location_id,
        )
        .where(InventoryUsedItem.status == UsedInventoryStatus.AVAILABLE)
    ).all()

    for _, location in used_inventory_rows:
        zone_used_stock[location.zone] += 1

    zones = sorted(set(zone_new_stock) | set(zone_used_stock))
    zone_stocks = [
        InboundDashboardZoneItem(
            zone=zone,
            new_stock_quantity=zone_new_stock[zone],
            used_stock_quantity=zone_used_stock[zone],
            available_quantity=(zone_new_stock[zone] + zone_used_stock[zone]),
        )
        for zone in zones
    ]

    recent_rows = session.exec(
        select(InboundItem, InboundJob, Book, Location)
        .join(
            InboundJob,
            InboundJob.id == InboundItem.inbound_job_id,
        )
        .join(
            Book,
            Book.id == InboundItem.book_id,
        )
        .outerjoin(
            Location,
            Location.id == InboundItem.location_id,
        )
        .order_by(InboundItem.created_at.desc())
        .limit(10)
    ).all()

    recent_activities = [
        RecentInboundActivityResponse(
            inbound_item_id=inbound_item.id,
            book_title=book.title,
            inbound_type=inbound_job.inbound_type,
            inbound_status=inbound_job.status,
            quantity=inbound_item.quantity,
            location_barcode=(location.barcode if location is not None else None),
            occurred_at=inbound_item.created_at,
        )
        for inbound_item, inbound_job, book, location in recent_rows
    ]

    return InboundDashboardSummaryResponse(
        today_inbound_quantity=today_inbound_quantity,
        completed_inspection_count=completed_inspection_count,
        pending_inspection_count=pending_inspection_count,
        recheck_required_count=recheck_required_count,
        daily_inbound_trend=daily_inbound_trend,
        grade_distribution=grade_distribution,
        zone_stocks=zone_stocks,
        recent_activities=recent_activities,
    )
