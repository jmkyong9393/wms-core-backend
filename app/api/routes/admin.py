from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.core.database import engine
from app.api.dependencies.auth import require_master
from app.models.wms import ReturnJob, User

router = APIRouter()


# 관리자 대시보드용 AI 검수 작업 지표 조회 API
@router.get("/inspection-metrics")
def get_inspection_metrics(
    current_master: User = Depends(require_master),
) -> Dict[str, Any]:
    with Session(engine) as session:
        statement = select(ReturnJob).where(
            ReturnJob.tenant_id == current_master.tenant_id,
        )
        jobs = session.exec(statement).all()

        total_jobs = len(jobs)

        pending_jobs = 0
        processing_jobs = 0
        approved_jobs = 0
        rejected_jobs = 0
        failed_jobs = 0

        processing_times = []

        for job in jobs:
            if job.status == "PENDING":
                pending_jobs += 1

            elif job.status == "PROCESSING":
                processing_jobs += 1

            elif job.status == "APPROVED":
                approved_jobs += 1

            elif job.status == "REJECTED":
                rejected_jobs += 1

            elif job.status == "FAILED":
                failed_jobs += 1

            if job.status in ["APPROVED", "REJECTED"]:
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
            "approved_jobs": approved_jobs,
            "rejected_jobs": rejected_jobs,
            "failed_jobs": failed_jobs,
            "average_processing_time_seconds": average_processing_time,
        }