from datetime import datetime
from uuid import UUID
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.database import get_session
from app.api.dependencies.auth import require_admin_or_master
from app.models.wms import ReturnJob, ReturnJobStatus, User
from app.schemas.admin_inspection import (
    InspectionDetailResponse,
    InspectionHistoryRow,
)
from app.services.admin_inspection_service import (
    get_inspection_detail,
    get_inspection_history,
)

router = APIRouter()


# 관리자용 전체 검수 이력 그리드 조회 API
@router.get("/inspection-metrics")
def get_inspection_metrics(
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
):
    statement = select(ReturnJob).where(
        ReturnJob.tenant_id == current_admin.tenant_id,
    )
    jobs = session.exec(statement).all()

    total_jobs = len(jobs)

    pending_jobs = 0
    processing_jobs = 0
    approved_jobs = 0
    rejected_jobs = 0
    failed_jobs = 0
    hitl_required_jobs = 0
    recheck_required_jobs = 0

    processing_times = []

    for job in jobs:
        if job.status == ReturnJobStatus.PENDING:
            pending_jobs += 1

        elif job.status == ReturnJobStatus.PROCESSING:
            processing_jobs += 1

        elif job.status == ReturnJobStatus.HITL_REQUIRED:
            hitl_required_jobs += 1

        elif job.status == ReturnJobStatus.RECHECK_REQUIRED:
            recheck_required_jobs += 1

        elif job.status == ReturnJobStatus.APPROVED:
            approved_jobs += 1

        elif job.status == ReturnJobStatus.REJECTED:
            rejected_jobs += 1

        elif job.status == ReturnJobStatus.FAILED:
            failed_jobs += 1


        if job.status in {
            ReturnJobStatus.APPROVED,
            ReturnJobStatus.REJECTED,
        }:  
            # 요청 생성부터 WMS 최종 처리 완료까지의 평균 시간
            processing_time = (
                job.updated_at - job.created_at
            ).total_seconds()

            processing_times.append(processing_time)

    if processing_times:
        average_processing_time = (
            sum(processing_times) / len(processing_times)
        )
    else:
        average_processing_time = 0

    return {
        "total_jobs": total_jobs,
        "pending_jobs": pending_jobs,
        "processing_jobs": processing_jobs,
        "hitl_required_jobs": hitl_required_jobs,
        "recheck_required_jobs": recheck_required_jobs,
        "approved_jobs": approved_jobs,
        "rejected_jobs": rejected_jobs,
        "failed_jobs": failed_jobs,
        "average_processing_time_seconds": average_processing_time,
    }

# 관리자용 검수 현황 통계 API
@router.get(
    "/inspections",
    response_model=list[InspectionHistoryRow],
)
def get_admin_inspection_history(
    status: ReturnJobStatus | None = Query(
        default=None,
        description="검수 상태 필터(APPROVED, FAILED 등)",
    ),
    start_date: datetime | None = Query(
        default=None,
        description="조회 시작 날짜",
    ),
    end_date: datetime | None = Query(
        default=None,
        description="조회 종료 날짜",
    ),
    keyword: str | None = Query(
        default=None,
        description="도서명 검색 키워드",
    ),
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> list[InspectionHistoryRow]:

    return get_inspection_history(
        session=session,
        tenant_id=current_admin.tenant_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
    )

# 관리자용 개별 검수 상세 조회 API
@router.get(
    "/inspections/{job_id}",
    response_model=InspectionDetailResponse,
)
def get_admin_inspection_detail(
    job_id: UUID,
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> InspectionDetailResponse:
    return get_inspection_detail(
        session=session,
        tenant_id=current_admin.tenant_id,
        job_id=job_id,
    )