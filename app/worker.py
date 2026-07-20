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
    process_wms_result_with_lock,
    save_inspection_failed,
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
    return_job_id: UUID,
) -> tuple[str, dict[str, Any]]:
    idempotency_key = f"return-job:{return_job_id}"

    if decision == "APPROVE":
        wms_result = call_wms_approve_api(
            book_id=str(book_id),
            idempotency_key=idempotency_key,
        )

        return ReturnJobStatus.APPROVED.value, {
            "wms_result": wms_result,
            "idempotency_key": idempotency_key,
        }

    if decision == "REJECT":
        reject_reason = "AI_INSPECTION_REJECTED"

        wms_result = call_wms_reject_api(
            book_id=str(book_id),
            reason=reject_reason,
            idempotency_key=idempotency_key,
        )

        return ReturnJobStatus.REJECTED.value, {
            "wms_result": wms_result,
            "reject_reason": reject_reason,
            "idempotency_key": idempotency_key,
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

    if failed_job is None:
        return

    if failed_job.status != ReturnJobStatus.FAILED:
        logger.warning(
            "작업이 이미 최종 처리되어 FAILED 이벤트 발행을 생략합니다. "
            "job_id=%s status=%s",
            failed_job.id,
            failed_job.status,
        )
        return

    publish_event_safely(
        event_name="FAILED",
        publish_function=publish_failed_event,
        job_id=failed_job.id,
        task_id=task_id,
        error=error,
    )

# Redis 알림은 상태 전달용, 실패해도 핵심 검수 트랜잭션까지 실패 시키지 않음.
def publish_event_safely(
    event_name: str,
    publish_function,
    **kwargs: Any,
) -> bool:
    try:
        publish_function(**kwargs)
        return True
    except Exception:
        logger.exception(
            "Redis 이벤트 발행에 실패했습니다. "
            "event=%s kwargs=%s",
            event_name,
            kwargs,
        )
        return False    

# celery task
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
            tenant_id,
            book_id,
            mode,
            image_paths,
        ) = prepare_processing_job(
            return_job_id=return_job_id,
            celery_task_id=task_id,
        )

        # 2. PROCESSING 상태 실시간 전달
        publish_event_safely(
            event_name="PROCESSING",
            publish_function=publish_processing_event,
            job_id=job_id,
            task_id=task_id,
        )

        # 3. LangGraph 실행
        ai_result = LangGraphInspectionWrapper().run_inspection(
            job_id=job_id,
            tenant_id=tenant_id,
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

        # 4~5. DB Lock을 획득한 뒤 WMS 호출 및 최종 결과 저장
        job, wms_called = process_wms_result_with_lock(
            return_job_id=job_id,
            ai_result=ai_result,
            execute_wms_action=execute_wms_action,
        )

        if not wms_called:
            logger.warning(
                "WMS 중복 호출이 차단되었습니다. "
                "task_id=%s job_id=%s status=%s",
                task_id,
                job.id,
                job.status,
            )

        # 6. 최종 상태 실시간 전달
        publish_event_safely(
            event_name="FINAL",
            publish_function=publish_final_event,
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
            "tenant_id": str(job.tenant_id),
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