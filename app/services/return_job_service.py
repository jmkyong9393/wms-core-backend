import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from sqlmodel import Session, select

from app.core.database import engine
from app.models.wms import ReturnJob, ReturnJobStatus


logger = logging.getLogger(__name__)


# job_id로 ReturnJob을 조회하는 공통 함수
def find_return_job_by_id(
    session: Session,
    return_job_id: uuid.UUID,
) -> Optional[ReturnJob]:
    statement = select(ReturnJob).where(
        ReturnJob.id == return_job_id
    )

    return session.exec(statement).first()


# ReturnJob 상태를 변경하고 DB에 저장
def update_return_job_status(
    session: Session,
    job: ReturnJob,
    status: ReturnJobStatus,
) -> None:
    job.status = status
    job.updated_at = datetime.utcnow()

    session.add(job)
    session.commit()
    session.refresh(job)


# Celery 작업 시작 전 ReturnJob을 조회하고
# PROCESSING 상태로 변경
def prepare_processing_job(
    return_job_id: str,
    celery_task_id: str,
) -> Tuple[
    uuid.UUID,
    uuid.UUID,
    str,
    list[str],
]:
    # Celery에서 문자열로 받은 ID를 UUID로 변환
    parsed_return_job_id = uuid.UUID(return_job_id)

    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            return_job_id=parsed_return_job_id,
        )

        if job is None:
            raise ValueError(
                "ReturnJob을 찾을 수 없습니다. "
                f"return_job_id={return_job_id}"
            )

        # inspections.py에서 task_id가 저장되지 않은 경우를 대비
        if job.task_id is None:
            job.task_id = celery_task_id
            job.updated_at = datetime.utcnow()

            session.add(job)
            session.commit()
            session.refresh(job)

        # Worker가 작업을 시작했으므로 PROCESSING으로 변경
        update_return_job_status(
            session=session,
            job=job,
            status=ReturnJobStatus.PROCESSING,
        )

        # mode가 Enum 또는 문자열인 경우를 모두 처리
        mode = (
            job.mode.value
            if hasattr(job.mode, "value")
            else str(job.mode)
        )

        # DB의 image_urls를 Worker 내부 image_paths로 사용
        image_paths = list(job.image_urls or [])

        if not image_paths:
            raise ValueError(
                "검수할 이미지가 없습니다."
                f"return_job_id={return_job_id}"
            )
        
        # Worker에서 LangGraph 실행에 필요한 값 반환
        return (
            job.id,
            job.book_id,
            mode,
            image_paths,
        )

# LangGraph 결과와 WMS API 결과를 DB에 저장
def save_inspection_result(
    return_job_id: uuid.UUID,
    ai_result: Dict[str, Any],
    final_status: str,
    extra_logs: Dict[str, Any],
) -> ReturnJob:
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            return_job_id=return_job_id,
        )

        if job is None:
            raise ValueError(
                "ReturnJob을 찾을 수 없습니다. "
                f"return_job_id={return_job_id}"
            )

        # AI 결과 저장
        job.ubci_score = ai_result.get("ubci_score")
        job.final_report = ai_result.get("final_report")

        # Agent 결과와 WMS 호출 결과를 하나의 로그로 저장
        job.agent_logs = {
            **(ai_result.get("agent_logs") or {}),
            **extra_logs,
        }

        # APPROVED 또는 REJECTED 상태 저장
        job.status = ReturnJobStatus(final_status)
        job.updated_at = datetime.utcnow()

        session.add(job)
        session.commit()
        session.refresh(job)

        return job


# 재시도 이후에도 실패하거나
# LangGraph 처리 중 오류가 발생한 경우 FAILED 저장
def save_inspection_failed(
    return_job_id: uuid.UUID,
    celery_task_id: str,
    error: Exception,
) -> Optional[ReturnJob]:
    with Session(engine) as session:
        job = find_return_job_by_id(
            session=session,
            return_job_id=return_job_id,
        )

        if job is None:
            logger.error(
                (
                    "FAILED 상태 저장 실패: "
                    "ReturnJob을 찾을 수 없습니다. "
                    "return_job_id=%s"
                ),
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

        job.updated_at = datetime.utcnow()

        session.add(job)
        session.commit()
        session.refresh(job)

        return job