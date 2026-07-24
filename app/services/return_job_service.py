import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, select

from app.core.database import engine
from app.models.wms import ConditionGrade, ReturnJob, ReturnJobStatus


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

# Lock 조회 함수 추가
def find_return_job_by_id_for_update(
        session: Session,
        job_id: UUID,
) -> ReturnJob | None:
    statement = (
        select(ReturnJob)
        .where(ReturnJob.id == job_id)
        .with_for_update()
    )

    return session.exec(statement).first()

# 조회 중복 방지
def get_return_job_or_raise(
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
    UUID,       # job_id
    UUID,       # tenant_id
    UUID,       # book_id
    str,        # mode
    list[str],  # image_paths
]:
    job_id = UUID(return_job_id)

    with Session(engine) as session:
        job = find_return_job_by_id_for_update(
            session=session,
            job_id=job_id,
        )

        if job is None:
            raise ValueError(
                "ReturnJob을 찾을 수 없습니다. "
                f"job_id={job_id}"
            )

        if job.status in {
            ReturnJobStatus.APPROVED,
            ReturnJobStatus.REJECTED,
            ReturnJobStatus.FAILED,
        }:
            raise ValueError(
                "이미 종료된 검수 작업입니다. "
                f"job_id={job_id} "
                f"status={job.status}"
            )

        if job.task_id is None:
            job.task_id = celery_task_id
        elif job.task_id != celery_task_id:
            raise ValueError(
                "이미 다른 Celery 작업이 할당된 검수입니다. "
                f"job_id={job_id} "
                f"existing_task_id={job.task_id}"
            )

        image_paths = list(job.image_paths or [])

        if not image_paths:
            raise ValueError(
                "검수할 이미지가 없습니다. "
                f"job_id={job_id}"
            )

        job.status = ReturnJobStatus.PROCESSING
        job.updated_at = datetime.utcnow()

        session.add(job)
        session.commit()
        session.refresh(job)

        mode = (
            job.mode.value
            if hasattr(job.mode, "value")
            else str(job.mode)
        )

        return (
            job.id,
            job.tenant_id,
            job.book_id,
            mode,
            image_paths,
        )


# 재시도 이후에도 실패하거나
# LangGraph 처리 중 오류가 발생한 경우 FAILED 저장
def save_inspection_failed(
    return_job_id: UUID,
    celery_task_id: str,
    error: Exception,
) -> ReturnJob | None:
    with Session(engine) as session:
        job = find_return_job_by_id_for_update(
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

        if job.status in {
            ReturnJobStatus.APPROVED,
            ReturnJobStatus.REJECTED,
        }:
            logger.warning(
                "이미 최종 처리된 작업이므로 FAILED 상태로 변경하지 않습니다. "
                "job_id=%s status=%s error=%s",
                return_job_id,
                job.status,
                error,
            )
            return job
        
        if job.status == ReturnJobStatus.FAILED:
            logger.warning(
                "이미 FAILED 처리된 작업이므로 중복 실패 저장을 생략합니다. "
                "job_id=%s error=%s",
                return_job_id,
                error,
            )
            return None

        job.status = ReturnJobStatus.FAILED
        job.final_report = (
            "AI 검수 처리 중 오류가 발생했습니다."
        )

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

# ReturnJob 상태를 잠금으로 확인한 뒤, WMS 호출이 끝나면 다시 잠가 결과를 저장한다.
def process_wms_result_with_lock(
    return_job_id: UUID,
    ai_result: dict[str, Any],
    execute_wms_action,
) -> tuple[ReturnJob, bool]:
    target_location_id: UUID | None
    with Session(engine) as session:
        job = find_return_job_by_id_for_update(
            session=session,
            job_id=return_job_id,
        )

        if job is None:
            raise ValueError(
                "ReturnJob을 찾을 수 없습니다. "
                f"job_id={return_job_id}"
            )

        # 이미 최종 처리된 작업이면 WMS를 다시 호출하지 않음
        if job.status in {
            ReturnJobStatus.APPROVED,
            ReturnJobStatus.REJECTED,
        }:
            logger.warning(
                "이미 완료된 검수 작업입니다. "
                "WMS 재호출을 생략합니다. "
                "job_id=%s status=%s",
                return_job_id,
                job.status,
            )

            return job, False

        # WMS 호출은 PROCESSING 상태에서만 허용
        if job.status != ReturnJobStatus.PROCESSING:
            raise ValueError(
                "WMS 처리가 가능한 상태가 아닙니다. "
                f"job_id={return_job_id} "
                f"status={job.status}"
            )

        decision = ai_result.get("decision")

        if decision not in {"APPROVE", "REJECT"}:
            raise ValueError(
                f"지원하지 않는 AI 판정입니다: {decision}"
            )

        target_location_id = job.target_location_id

    # 내부 WMS API도 ReturnJob을 조회하므로 HTTP 호출 중에는 행 잠금을 유지하지 않는다.
    (
        final_status,
        wms_logs,
        condition_grade,
        normalized_ubci_score,
    ) = execute_wms_action(
        decision=decision,
        return_job_id=return_job_id,
        ai_result=ai_result,
        target_location_id=target_location_id,
    )

    with Session(engine) as session:
        job = find_return_job_by_id_for_update(
            session=session,
            job_id=return_job_id,
        )

        if job is None:
            raise ValueError(
                "ReturnJob을 찾을 수 없습니다. "
                f"job_id={return_job_id}"
            )

        if job.status in {
            ReturnJobStatus.APPROVED,
            ReturnJobStatus.REJECTED,
        }:
            return job, False

        if job.status != ReturnJobStatus.PROCESSING:
            raise ValueError(
                "WMS 결과를 저장할 수 없는 상태입니다. "
                f"job_id={return_job_id} "
                f"status={job.status}"
            )

        job.ubci_score = normalized_ubci_score
        job.condition_grade = ConditionGrade(condition_grade)
        job.final_report = ai_result.get("final_report")
        job.agent_logs = {
            **(ai_result.get("agent_logs") or {}),
            **wms_logs,
        }
        job.status = ReturnJobStatus(final_status)
        job.updated_at = datetime.utcnow()

        session.add(job)
        session.commit()
        session.refresh(job)

        return job, True
