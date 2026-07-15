import logging
import uuid
from typing import Any, Dict, Tuple

import httpx

from app.core.celery_app import celery_app
from app.models.wms import ReturnJob
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


# PROCESSING 상태를 프론트에 전달
def publish_processing_event(
    return_job_id: uuid.UUID,
    celery_task_id: str,
) -> None:
    publish_return_job_event(
        return_job_id=str(return_job_id),
        event={
            # 외부 응답 필드명은 job_id로 통일
            "job_id": str(return_job_id),
            "task_id": celery_task_id,
            "status": "PROCESSING",
            "progress": 50,
        },
    )


# 최종 검수 결과를 프론트에 전달
def publish_final_event(
    job: ReturnJob,
    celery_task_id: str,
) -> None:
    publish_return_job_event(
        return_job_id=str(job.id),
        event={
            "job_id": str(job.id),
            "task_id": celery_task_id,
            "status": job.status,
            "progress": 100,
            "ubci_score": job.ubci_score,
        },
    )


# Worker 처리 실패 상태를 프론트에 전달
def publish_failed_event(
    return_job_id: uuid.UUID,
    celery_task_id: str,
    error: Exception,
) -> None:
    publish_return_job_event(
        return_job_id=str(return_job_id),
        event={
            "job_id": str(return_job_id),
            "task_id": celery_task_id,
            "status": "FAILED",
            "progress": 100,
            "error_message": str(error),
        },
    )


# AI 판정에 따라 WMS 승인 또는 반려 API 호출
def execute_wms_action(
    decision: str,
    book_id: uuid.UUID,
) -> Tuple[str, Dict[str, Any]]:
    if decision == "APPROVE":
        wms_result = call_wms_approve_api(
            book_id=str(book_id),
        )

        return "APPROVED", {
            "wms_result": wms_result,
        }

    if decision == "REJECT":
        reject_reason = "AI_INSPECTION_REJECTED"

        wms_result = call_wms_reject_api(
            book_id=str(book_id),
            reason=reject_reason,
        )

        return "REJECTED", {
            "wms_result": wms_result,
            "reject_reason": reject_reason,
        }

    raise ValueError(
        f"Unknown decision: {decision}"
    )

@celery_app.task(
    bind=True,
    name="app.worker.process_inspection",
    max_retries=3,          # 최대 3번 재시도
    default_retry_delay=5,  # 기본 5초 후 재시도
)
def process_inspection(
    self,
    return_job_id: str,
) -> Dict[str, Any]:
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

    # Flower와 Celery에서 사용하는 비동기 작업 ID
    celery_task_id = self.request.id

    # 문자열 ID를 UUID로 변환
    parsed_return_job_id = uuid.UUID(return_job_id)

    try:
        logger.info(
            "process_inspection started. task_id=%s job_id=%s",
            celery_task_id,
            return_job_id,
        )

        # 1. ReturnJob 조회 및 PROCESSING 상태 변경
        #
        # 변경된 prepare_processing_job은 다음 값을 반환해야 함:
        # return_job_id, book_id, mode, image_paths
        (
            parsed_return_job_id,
            book_id,
            mode,
            image_paths,
        ) = prepare_processing_job(
            return_job_id=return_job_id,
            celery_task_id=celery_task_id,
        )

        # 2. PROCESSING 상태 실시간 전달
        publish_processing_event(
            return_job_id=parsed_return_job_id,
            celery_task_id=celery_task_id,
        )

        logger.info(
            "published inspection event. job_id=%s status=PROCESSING",
            parsed_return_job_id,
        )

        # 3. LangGraph 실행
        langgraph_wrapper = LangGraphInspectionWrapper()

        ai_result = langgraph_wrapper.run_inspection(
            book_id=book_id,
            mode=mode,
            image_paths=image_paths,
        )

        # AI 최종 판정 확인
        decision = ai_result.get("decision")

        if decision not in ["APPROVE", "REJECT"]:
            raise ValueError(
                f"Unknown decision: {decision}"
            )

        # 4. AI 판정에 따라 WMS 승인 또는 반려 처리
        final_status, extra_logs = execute_wms_action(
            decision=decision,
            book_id=book_id,
        )

        # 5. AI 결과와 WMS 결과를 DB에 저장
        job = save_inspection_result(
            return_job_id=parsed_return_job_id,
            ai_result=ai_result,
            final_status=final_status,
            extra_logs=extra_logs,
        )

        # 6. 최종 상태 실시간 전달
        publish_final_event(
            job=job,
            celery_task_id=celery_task_id,
        )

        logger.info(
            "process_inspection completed. task_id=%s job_id=%s status=%s",
            celery_task_id,
            job.id,
            job.status,
        )

        return {
            "task_id": celery_task_id,
            "job_id": str(job.id),
            "book_id": str(job.book_id),
            "status": job.status,
            "ubci_score": job.ubci_score,
        }

    # WMS API 등 HTTP 통신 오류
    except httpx.HTTPError as error:
        logger.warning(
            (
                "HTTP error occurred. "
                "task_id=%s job_id=%s retry=%s/%s error=%s"
            ),
            celery_task_id,
            return_job_id,
            self.request.retries,
            self.max_retries,
            str(error),
        )

        # 남은 재시도 횟수가 있으면 다시 실행
        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=error,
                countdown=5,
            )

        logger.exception(
            (
                "HTTP retry exhausted. "
                "Marking ReturnJob as FAILED. "
                "task_id=%s job_id=%s"
            ),
            celery_task_id,
            return_job_id,
        )

        failed_job = save_inspection_failed(
            return_job_id=parsed_return_job_id,
            celery_task_id=celery_task_id,
            error=error,
        )

        if failed_job is not None:
            publish_failed_event(
                return_job_id=failed_job.id,
                celery_task_id=celery_task_id,
                error=error,
            )

        raise

    # LangGraph, DB 처리 등의 일반 오류
    except Exception as error:
        logger.exception(
            "process_inspection failed. task_id=%s job_id=%s",
            celery_task_id,
            return_job_id,
        )

        failed_job = save_inspection_failed(
            return_job_id=parsed_return_job_id,
            celery_task_id=celery_task_id,
            error=error,
        )

        if failed_job is not None:
            publish_failed_event(
                return_job_id=failed_job.id,
                celery_task_id=celery_task_id,
                error=error,
            )

        raise