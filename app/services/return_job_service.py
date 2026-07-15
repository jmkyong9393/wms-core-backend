import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.database import engine
from app.models.wms import ReturnJob, ReturnJobStatus


logger = logging.getLogger(__name__)


# job_id로 ReturnJob을 조회하는 공통 함수
def find_return_job_by_id(
    session: Session,
    job_id: UUID,
) -> ReturnJob | None:
    statement = select(ReturnJob).where(
        ReturnJob.id == job_id
    )

    return session.exec(statement).first()

# 조회 중복 방지
def get_return_job_or_rasie(
        session: Session,
        job_id: UUID,
) -> ReturnJob:
    job = find_return_job_by_id(
        session=session,
        job_id=job_id,
    )

    if job is None:
        raise ValueError(
            "ReturnJob을 찾을 수 없습니다."
            f"job_id={job_id}"
        )
    
    return job

# db 저장 코드 통일
def save_return_job(
        session: Session,
        job: ReturnJob,
) -> None:
    job.updated_at = datetime.utcnow()

    session.add(job)
    session.commit()
    session.refresh(job)


# ReturnJob 상태를 변경하고 DB에 저장
def update_return_job_status(
    session: Session,
    job: ReturnJob,
    status: ReturnJobStatus,
) -> None:
    job.status = status
    save_return_job(
        session=session,
        job=job,
    )


# Celery 작업 시작 전 ReturnJob을 조회하고
# PROCESSING 상태로 변경
def prepare_processing_job(
    return_job_id: str,
    celery_task_id: str,
) -> tuple[
    UUID,
    UUID,
    str,
    list[str],
]:
    job_id = UUID(return_job_id)

    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            job_id=job_id,
        )

        image_paths = list(job.image_paths or [])

        if not image_paths:
            raise ValueError(
                "검수할 이미지가 없습니다."
                f"job_id={job_id}"
            )

        if job.task_id is None:
            job.task_id = celery_task_id

        job.status = ReturnJobStatus.PROCESSING

        save_return_job(
            session=session,
            job=job,
        )

        mode = (
            job.mode.value
            if hasattr(job.mode, "value")
            else str(job.mode)
        )

        return (
            job.id,
            job.book_id,
            mode,
            image_paths,
        )


# LangGraph 결과와 WMS API 결과를 DB에 저장
def save_inspection_result(
    return_job_id: UUID,
    ai_result: dict[str, Any],
    final_status: str,
    extra_logs: dict[str, Any],
) -> ReturnJob:
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            job_id=return_job_id,
        )

        # AI 결과 저장
        job.ubci_score = ai_result.get("ubci_score")
        job.final_report = ai_result.get("final_report")
        job.agent_logs = {
            **(ai_result.get("agent_logs") or {}),
            **extra_logs,
        }
        job.status = ReturnJobStatus(final_status)
        
        save_return_job(
            session=session,
            job=job,
        )

        return job


# 재시도 이후에도 실패하거나
# LangGraph 처리 중 오류가 발생한 경우 FAILED 저장
def save_inspection_failed(
    return_job_id: UUID,
    celery_task_id: str,
    error: Exception,
) -> ReturnJob | None:
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            job_id=return_job_id,
        )

        if job is None:
            logger.error(
                    "FAILED 상태 저장 실패: "
                    "ReturnJob을 찾을 수 없습니다. "
                    "job_id=%s",
                    return_job_id,
            )
            return None

        job.status = ReturnJobStatus.FAILED
        job.final_report = (
            "AI 검수 처리 중 오류가 발생했습니다."
        )

        # 기존 로그가 있다면 유지하고 error 정보 추가
        job.agent_logs = {
            **(job.agent_logs or {}),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "task_id": celery_task_id,
            },
        }

        save_return_job(
            session=session,
            job=job,
        )

        return job