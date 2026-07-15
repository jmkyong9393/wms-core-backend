import logging
from typing import Any
from uuid import UUID

import httpx

from app.core.celery_app import celery_app
from app.models.wms import ReturnJob, ReturnJobStatus
from app.services.langgraph_wrapper import LangGraphInspectionWrapper
from app.services.redis_pubsub import publish_return_job_event
from app.services.return_job_service import (
    prepare_processing_job,
    save_inspection_failed,
    save_inspection_result,
)
from app.services.wms_client import (
    call_wms_approve_api,
    call_wms_reject_api,
)

logger = logging.getLogger(__name__)

TASK_NAME = "app.worker.process_inspection"
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


# PROCESSING 상태를 프론트에 전달
def publish_processing_event(
    job_id: UUID,
    task_id: str,
) -> None:
    publish_return_job_event(
        job_id=str(job_id),
        event={
            # 외부 응답 필드명은 job_id로 통일
            "job_id": str(job_id),
            "task_id": task_id,
            "status": ReturnJobStatus.PROCESSING.value,
            "progress": 50,
        },
    )


# 최종 검수 결과를 프론트에 전달
def publish_final_event(
    job: ReturnJob,
    task_id: str,
) -> None:
    status = (
        job.status.value
        if hasattr(job.status, "value")
        else str(job.status)
    )

    publish_return_job_event(
        job_id=str(job.id),
        event={
            "job_id": str(job.id),
            "task_id": task_id,
            "status": status,
            "progress": 100,
            "ubci_score": job.ubci_score,
        },
    )


# Worker 처리 실패 상태를 프론트에 전달
def publish_failed_event(
    job_id: UUID,
    task_id: str,
    error: Exception,
) -> None:
    publish_return_job_event(
        job_id=str(job_id),
        event={
            "job_id": str(job_id),
            "task_id": task_id,
            "status": ReturnJobStatus.FAILED.value,
            "progress": 100,
            "error_message": str(error),
        },
    )


# AI 판정에 따라 WMS 승인 또는 반려 API 호출
def execute_wms_action(
    decision: str,
    book_id: UUID,
) -> tuple[str, dict[str, Any]]:
    if decision == "APPROVE":
        wms_result = call_wms_approve_api(
            book_id=str(book_id),
        )

        return ReturnJobStatus.APPROVED.value, {
            "wms_result": wms_result,
        }

    if decision == "REJECT":
        reject_reason = "AI_INSPECTION_REJECTED"

        wms_result = call_wms_reject_api(
            book_id=str(book_id),
            reason=reject_reason,
        )

        return ReturnJobStatus.REJECTED.value, {
            "wms_result": wms_result,
            "reject_reason": reject_reason,
        }

    raise ValueError(
        f"지원하지 않는 AI 판정값입니다: {decision}"
    )

def handle_inspection_failure(
        job_id: UUID,
        task_id: str,
        error: Exception,
) -> None:
    failed_job = save_inspection_failed(
        return_job_id=job_id,
        celery_task_id=task_id,
        error=error,
    )

    if failed_job is not None:
        publish_failed_event(
            job_id=failed_job.id,
            task_id=task_id,
            error=error,
        )


@celery_app.task(
    bind=True,
    name=TASK_NAME,
    max_retries=MAX_RETRIES,          # 최대 3번 재시도
    default_retry_delay=RETRY_DELAY_SECONDS,  # 기본 5초 후 재시도
)
def process_inspection(
    self,
    return_job_id: str,
) -> dict[str, Any]:
    """
    LangGraph 기반 AI 검수 Celery 작업.

    처리 흐름:
    1. ReturnJob 조회 및 PROCESSING 변경
    2. Redis Pub/Sub PROCESSING 이벤트 발행
    3. LangGraph 검수 실행
    4. 판정 결과에 따라 WMS API 호출
    5. AI 및 WMS 결과를 DB에 저장
    6. 최종 상태를 Redis Pub/Sub로 전달
    """

    task_id = self.request.id
    job_id = UUID(return_job_id)

    try:
        logger.info(
            "Inspection started. task_id=%s job_id=%s",
            task_id,
            job_id,
        )

        # 1. ReturnJob 조회 및 PROCESSING 상태 변경
        (
            job_id,
            book_id,
            mode,
            image_paths,
        ) = prepare_processing_job(
            return_job_id=return_job_id,
            celery_task_id=task_id,
        )

        # 2. PROCESSING 상태 실시간 전달
        publish_processing_event(
            job_id=job_id,
            task_id=task_id,
        )

        # 3. LangGraph 실행
        ai_result = LangGraphInspectionWrapper().run_inspection(
            job_id=job_id,
            book_id=book_id,
            mode=mode,
            image_paths=image_paths,
        )

        # AI 최종 판정 확인
        decision = ai_result.get("decision")

        if decision not in ["APPROVE", "REJECT"]:
            raise ValueError(
                f"지원하지 않는 AI 판정입니다: {decision}"
            )

        # 4. AI 판정에 따라 WMS 승인 또는 반려 처리
        final_status, wms_logs = execute_wms_action(
            decision=decision,
            book_id=book_id,
        )

        # 5. AI 결과와 WMS 결과를 DB에 저장
        job = save_inspection_result(
            return_job_id=job_id,
            ai_result=ai_result,
            final_status=final_status,
            extra_logs=wms_logs,
        )

        # 6. 최종 상태 실시간 전달
        publish_final_event(
            job=job,
            task_id=task_id,
        )

        logger.info(
            "Inspection completed. task_id=%s job_id=%s status=%s",
            task_id,
            job.id,
            job.status,
        )

        return {
            "task_id": task_id,
            "job_id": str(job.id),
            "book_id": str(job.book_id),
            "status": (
                job.status.value
                if hasattr(job.status, "value")
                else str(job.status)
            ),
            "ubci_score": job.ubci_score,
        }

    # WMS API 등 HTTP 통신 오류
    except httpx.HTTPError as error:
        logger.warning(
            (
                "WMS request failed. "
                "task_id=%s job_id=%s retry=%s/%s error=%s"
            ),
            task_id,
            job_id,
            self.request.retries,
            self.max_retries,
            error,
        )

        # 남은 재시도 횟수가 있으면 다시 실행
        if self.request.retries < self.max_retries:
            raise self.retry(exc=error)

        logger.exception(
            "WMS retry exhausted. task_id=%s job_id=%s",
            task_id,
            job_id,
        )

        handle_inspection_failure(
            job_id=job_id,
            task_id=task_id,
            error=error,
        )
        raise

    # LangGraph, DB 처리 등의 일반 오류
    except Exception as error:
        logger.exception(
            "Inspection failed. task_id=%s job_id=%s",
            task_id,
            job_id,
        )

        handle_inspection_failure(
            job_id=job_id,
            task_id=task_id,
            error=error,
        )
        raise