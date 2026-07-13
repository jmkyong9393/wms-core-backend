import logging
import uuid
from typing import Any, Dict, Tuple

import httpx

from app.models.wms import ReturnJob
from app.core.celery_app import celery_app
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


# PROCESSING 상태 변경 후 프론트에 진행 상태를 전달하는 Pub/Sub 이벤트 발행 함수
def publish_processing_event(
        return_job_id: uuid.UUID,
        celery_task_id: str,
) -> None:
    publish_return_job_event(
        return_job_id=str(return_job_id),
        event={
            "return_job_id": str(return_job_id),
            "task_id": celery_task_id,
            "status": "PROCESSING",
            "progress": 50,
        },
    )

# 최종 검수 결과(APPROVED/REJECTED)를 프론트에 전달하는 Pub/Sub 이벤트 발행 함수
def publish_final_event(
        job: ReturnJob,
        celery_task_id: str,
) -> None:
    publish_return_job_event(
        return_job_id=str(job.id),
        event={
            "return_job_id": str(job.id),
            "task_id": celery_task_id,
            "status": job.status,
            "progress": 100,
            "ubci_score": job.ubci_score,
        },
    )

# Worker 처리 실패 시 FAILED 상태를 프론트에 전달하는 Pub/Sub 이벤트 발행 함수
def publish_failed_event(
        return_job_id: uuid.UUID,
        celery_task_id: str,
        error: Exception,
) -> None:
    publish_return_job_event(
        return_job_id=str(return_job_id),
        event={
            "return_job_id": str(return_job_id),
            "task_id": celery_task_id,
            "status": "FAILED",
            "progress": 100,
            "error_message": str(error),
        },
    )

# AI decision 결과에 따라 WMS API를 호출하고 최종 ReturnJob status를 결정하는 함수
def execute_wms_action(
        decision: str,
        book_id: uuid.UUID,
) -> Tuple[str, Dict[str, Any]]:
    
    # APPROVE인 경우
    if decision == "APPROVE":

        wms_result = call_wms_approve_api(
            book_id = str(book_id),
        )
        return "APPROVED", {
            "wms_result" : wms_result,
        }
    
    # REJECT인 경우
    if decision == "REJECT":
        reject_reason = "AI_INSPECTION_REJECTED"
            
        wms_result = call_wms_reject_api(
            book_id = str(book_id),
            reason = reject_reason,
        )
        
        return "REJECTED", {
            "wms_result": wms_result,
            "reject_reason": reject_reason,
        }

    raise ValueError(f"Unknown decision: {decision}")



# celery task
@celery_app.task(
        bind=True,
        name="app.worker.process_inspection",
        max_retries=3, #최대 3번 재시도
        default_retry_delay=5, #5초후 retry
        )
def process_inspection(self, return_job_id: str) -> Dict[str, Any]:
    """
    LangGraph 기반 AI 비전 검수 Celery Worker 작업.

    흐름:
    1. Celery task_id 확인
    2. ReturnJob 조회 및 PROCESSING 상태 변경
    3. LangGraph Supervisor 실행
    4. AI decision에 따라 WMS API 호출
    5. AI 결과와 WMS 결과를 ReturnJob에 저장

    """

    # 1. Celery task_id 확인
    celery_task_id = self.request.id
    parsed_return_job_id = uuid.UUID(return_job_id)
    try: 
        logger.info(
            "process_inspection started. task_id=%s return_job_id=%s",
            celery_task_id,
            return_job_id,
        )

        # 2. ReturnJob 조회 및 PROCESSING 상태 변경
        (
            parsed_return_job_id,
            book_id,
            order_id,
            image_url,
        ) = prepare_processing_job(
            return_job_id=return_job_id,
            celery_task_id=celery_task_id,
        )

        # Redis Pub/Sub에 PROCESSING 이벤트 발행
        publish_processing_event(
            return_job_id=parsed_return_job_id,
            celery_task_id=celery_task_id,
        )

        logger.info(
            "published return job event. return_job_id=%s status=%s",
            parsed_return_job_id,
            "PROCESSING"
        )

        # 3. LangGraph Supervisor 실행
        langgraph_wrapper = LangGraphInspectionWrapper()

        ai_result = langgraph_wrapper.run_inspection(
            order_id = order_id,
            image_url = image_url
        )

        # 4. AI decision에 따라 WMS API 호출
        decision = ai_result.get("decision")

        if decision not in ["APPROVE","REJECT"]:
            raise ValueError(
                f"Unknown decision: {decision}"
            )


        final_status, extra_logs = execute_wms_action(
            decision=decision,
            book_id=book_id,
        )

        # 5. AI 결과와 WMS 결과를 ReturnJob에 저장
        job = save_inspection_result(
            return_job_id=parsed_return_job_id,
            ai_result=ai_result,
            final_status=final_status,
            extra_logs=extra_logs,

        )

        # Redis Pub/Sub에 최종 상태 이벤트 발행
        publish_final_event(
            job=job,
            celery_task_id=celery_task_id,
        )

        logger.info(
            "process_inspection completed. task_id=%s return_job_id=%s status=%s",
            celery_task_id,
            job.id,
            job.status,
        )

        return {
            "task_id": celery_task_id,
            "return_job_id": str(job.id),
            "order_id": str(job.order_id),
            "book_id": str(job.book_id),
            "status": job.status,
            "ubci_score": job.ubci_score,
        }
    # HTTP 오류
    except httpx.HTTPError as error:
        logger.warning(
            "HTTP error occurred. task_id=%s return_job_id=%s retry=%s/%s error=%s",
            celery_task_id,
            return_job_id,
            self.request.retries,
            self.max_retries,
            str(error),
        )

        if self.request.retries < self.max_retries:
            raise self.retry(exc=error, countdown=5)

        logger.exception(
            "HTTP retry exhausted. Marking ReturnJob as FAILED. task_id=%s return_job_id=%s",
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
    
    # 일반 오류
    except Exception as error:
        logger.exception(
            "process_inspection failed. task_id=%s return_job_id=%s",
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




    

    


