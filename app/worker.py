import logging
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session
from app.core.celery_app import celery_app
from app.core.database import engine

from app.models.wms import (
    Book,
    NotificationCategory,
    NotificationSeverity,
    ReturnJob,
    ReturnJobStatus,
)

from app.domains.restock.restock_service import (
    create_restock_proposal_for_rejected_job,
    create_restock_proposal_for_safety_stock,
)
from app.services.langgraph_wrapper import LangGraphInspectionWrapper
from app.services.redis_pubsub import (
    publish_return_job_event,
    publish_tenant_notification_event,
)
from app.domains.inspections.return_job_service import (
    WMSTaskMismatchError,
    prepare_processing_job,
    process_saved_wms_result_with_lock,
    save_ai_inspection_result,
    save_inspection_failed,
    save_inspection_hitl_required,
    save_wms_dispatch_failed,
    save_wms_processing_failed,
    save_wms_task_id,
)
from app.services.wms_client import (
    WMSRetryableError,
    WMSNonRetryableError,
    call_wms_inspection_result_api,
)
from app.services.dlq_service import push_inspection_failure_to_dlq
from app.domains.notifications.notification_service import (
    create_committed_notification_for_tenant,
)

logger = logging.getLogger(__name__)

TASK_NAME = "app.worker.process_inspection"
WMS_TASK_NAME = "app.worker.process_wms_action"
RESTOCK_PROPOSAL_TASK_NAME = ("app.worker.process_restock_proposal")
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5
SAFETY_STOCK_RESTOCK_PROPOSAL_TASK_NAME = (
    "app.worker.process_safety_stock_restock_proposal"
)

class WMSTaskDispatchError(RuntimeError):
    """WMS 후속 Celery Task 등록에 실패한 경우."""


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

    progress = (
        70
        if status in {
            ReturnJobStatus.HITL_REQUIRED.value,
            ReturnJobStatus.RECHECK_REQUIRED.value,
        }
        else 100
    )

    wms_result = (job.agent_logs or {}).get("wms_result", {})

    publish_return_job_event(
        job_id=str(job.id),
        event={
            "job_id": str(job.id),
            "task_id": task_id,
            "status": status,
            "progress": progress,
            "ubci_score": (
                float(job.ubci_score)
                if job.ubci_score is not None
                else None
            ),
            "condition_grade": (
                job.condition_grade.value
                if job.condition_grade is not None
                else None
            ),
            "lpn_barcode": wms_result.get("lpn_barcode"),
            "inventory_changed": wms_result.get("inventory_changed", False),
            "rejected_item_id": wms_result.get("rejected_item_id"),
            "location_barcode": wms_result.get("location_barcode"),
        },
    )


# Worker 처리 실패 상태와 실패 단계를 프론트에 전달
def publish_failed_event(
    job_id: UUID,
    task_id: str,
    failure_stage: str,
    error: Exception,
) -> None:
    publish_return_job_event(
        job_id=str(job_id),
        event={
            "job_id": str(job_id),
            "task_id": task_id,
            "status": ReturnJobStatus.FAILED.value,
            "progress": 100,
            "failure_stage": failure_stage,
            "error_message": str(error),
        },
    )

# AI 결과는 보존하되 WMS 후속 Task 등록 실패를 프론트에 전달
def publish_wms_dispatch_failed_event(
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
            "failure_stage": "WMS_TASK_DISPATCH",
            "wms_dispatch_failed": True,
            "error_message": str(error),
        },
    )


# AI 판정에 따라 WMS 승인 또는 반려 API 호출
def execute_wms_action(
    decision: str,
    return_job_id: UUID,
    ai_result: dict[str, Any],
) -> tuple[str, dict[str, Any], str, int | float | None]:
    
    idempotency_key = f"return-job:{return_job_id}"

    agent_logs = ai_result.get("agent_logs") or {}
    defects = agent_logs.get("defects") or []
    raw_ubci_score = ai_result.get("ubci_score")

    ubci_score = (
        float(raw_ubci_score)
        if raw_ubci_score is not None
        else None
    )

    admin_decision_code = agent_logs.get("admin_decision_code")
    final_grade = agent_logs.get("final_grade")
    rejection_disposition = agent_logs.get("rejection_disposition")

    # 결함 없는 Fast-track MINT는 Policy Agent를 생략하므로 100점으로 정규화한다.
    if (
        decision == "APPROVE"
        and ubci_score is None
        and agent_logs.get("is_mint") is True
        and not defects
    ):
        ubci_score = 100.0

    wms_result = call_wms_inspection_result_api(
        return_job_id=str(return_job_id),
        decision=decision,
        ubci_score=ubci_score,
        defects=defects,
        idempotency_key=idempotency_key,
        admin_decision_code=admin_decision_code,
        final_grade=final_grade,
        rejection_disposition=rejection_disposition,
    )

    condition_grade = wms_result.get("condition_grade")

    if condition_grade is None:
        raise ValueError("WMS response does not include condition_grade")

    final_status = (
        ReturnJobStatus.APPROVED.value
        if decision == "APPROVE"
        else ReturnJobStatus.REJECTED.value
    )
    return final_status, {
        "wms_result": wms_result,
        "idempotency_key": idempotency_key,
    }, condition_grade, ubci_score


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

def create_agent_hitl_alert_safely(
    job: ReturnJob,
    ai_result: dict[str, Any],
    task_id: str,
) -> None:
    """HITL_REQUIRED 검수 건에 대한 관리자 알림을 저장·발행한다."""
    reason_code = ai_result.get("reason_code") or "UNKNOWN"

    try:
        notification_tenant_id, event = (
            create_committed_notification_for_tenant(
                tenant_id=job.tenant_id,
                category=NotificationCategory.AGENT_ALERT,
                severity=NotificationSeverity.MEDIUM,
                title="AI 검수 결과 관리자 확인 필요",
                message=(
                    "AI 검수가 자동 확정되지 않아 관리자 검수가 필요합니다. "
                    f"사유 코드: {reason_code}"
                ),
                payload={
                    "return_job_id": str(job.id),
                    "inspection_task_id": task_id,
                    "reason_code": reason_code,
                },
            )
        )
    except Exception:
        logger.exception(
            "Agent HITL 알림 DB 저장에 실패했습니다. "
            "job_id=%s task_id=%s",
            job.id,
            task_id,
        )
        return

    publish_event_safely(
        event_name="AGENT_ALERT",
        publish_function=publish_tenant_notification_event,
        tenant_id=notification_tenant_id,
        event=event,
    )

# WMS 후속 Task 등록 실패 정보를 DB에 저장한다.
# 저장 함수 자체에서 AI 결과는 유지하고 ReturnJob 상태를 FAILED로 변경한다.
# 실패 정보 저장 과정에서 추가 예외가 발생해도 원래 예외 처리를 방해하지 않는다.
def save_wms_dispatch_failed_safely(
    job_id: UUID,
    task_id: str,
    error: Exception,
) -> bool:
    try:
        save_wms_dispatch_failed(
            return_job_id=job_id,
            inspection_task_id=task_id,
            error=error,
        )
        return True

    except Exception:
        logger.exception(
            "WMS Task 등록 실패 정보를 DB에 저장하지 못했습니다. "
            "job_id=%s task_id=%s",
            job_id,
            task_id,
        )
        return False


# DLQ 저장 실패가 원래 작업 실패 처리까지 막지 않도록 안전하게 처리
def push_dlq_safely(
    job_id: UUID,
    task_id: str,
    source_task: str,
    error: Exception,
    retry_count: int,
) -> bool:
    try:
        push_inspection_failure_to_dlq(
            job_id=str(job_id),
            task_id=task_id,
            source_task=source_task,
            error=error,
            retry_count=retry_count,
        )
        return True

    except Exception:
        logger.exception(
            "DLQ 적재에 실패했습니다. "
            "job_id=%s task_id=%s source_task=%s",
            job_id,
            task_id,
            source_task,
        )
        return False
    

def handle_inspection_failure(
    job_id: UUID,
    task_id: str,
    source_task: str,
    error: Exception,
    retry_count: int,
) -> None:
    # WMS 실패는 기존 AI 결과를 보존하고 WMS 오류만 기록
    if source_task == WMS_TASK_NAME:
        failed_job = save_wms_processing_failed(
            return_job_id=job_id,
            wms_task_id=task_id,
            error=error,
        )
    else:
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

    failure_stage = (
        "WMS_PROCESSING"
        if source_task == WMS_TASK_NAME
        else "AI_INSPECTION"
    )

    publish_event_safely(
        event_name="FAILED",
        publish_function=publish_failed_event,
        job_id=failed_job.id,
        task_id=task_id,
        failure_stage=failure_stage,
        error=error,
    )

    push_dlq_safely(
        job_id=failed_job.id,
        task_id=task_id,
        source_task=source_task,
        error=error,
        retry_count=retry_count,
    )

def dispatch_restock_proposal_task_safely(
    return_job: ReturnJob,
) -> None:
    """
    최종 반려된 검수 작업에 대해 Restock 추천안 생성 Task를 등록한다.

    중복 등록되더라도 order_proposals.return_job_id 유니크 제약과
    서비스의 기존 추천안 조회로 실제 추천안은 한 번만 생성된다.
    """
    if return_job.status != ReturnJobStatus.REJECTED:
        return

    restock_task_id = str(uuid4())

    try:
        celery_app.send_task(
            RESTOCK_PROPOSAL_TASK_NAME,
            args=[str(return_job.id)],
            task_id=restock_task_id,
        )

        logger.info(
            "Restock proposal task queued. "
            "restock_task_id=%s return_job_id=%s",
            restock_task_id,
            return_job.id,
        )

    except Exception:
        # Restock 추천 생성 실패가 이미 완료된 WMS 반려 처리를
        # FAILED로 바꾸지 않도록 로그만 남긴다.
        logger.exception(
            "Restock proposal task dispatch failed. "
            "return_job_id=%s",
            return_job.id,
        )


@celery_app.task(
    bind=True,
    name=RESTOCK_PROPOSAL_TASK_NAME,
    max_retries=MAX_RETRIES,
    default_retry_delay=RETRY_DELAY_SECONDS,
)
def process_restock_proposal(
    self,
    return_job_id: str,
) -> dict[str, Any]:
    """
    최종 반려된 검수 건의 대체 발주 추천안을 생성한다.

    1. 반려 작업 기준 판매량·가용 재고·반려 수량 조회
    2. Restock Agent 호출
    3. OrderProposal 저장
    4. 관리자 RESTOCK_ALERT 알림 저장 및 SSE 발행
    """
    job_id = UUID(return_job_id)
    task_id = self.request.id

    try:
        with Session(engine) as session:
            result = create_restock_proposal_for_rejected_job(
                session=session,
                return_job_id=job_id,
            )

            if result.generation_in_progress:
                logger.warning(
                    "Restock Agent generation is in progress. "
                    "task_id=%s return_job_id=%s",
                    task_id,
                    job_id,
                )

                return {
                    "task_id": task_id,
                    "return_job_id": str(job_id),
                    "created": False,
                    "generation_in_progress": True,
                }

            proposal = result.proposal
            if proposal is None:
                raise RuntimeError(
                    "Restock proposal result does not include a proposal."
                )

            if not result.created:
                logger.info(
                    "Restock proposal already exists. "
                    "task_id=%s return_job_id=%s proposal_id=%s",
                    task_id,
                    job_id,
                    proposal.id,
                )
                return {
                    "task_id": task_id,
                    "return_job_id": str(job_id),
                    "proposal_id": str(proposal.id),
                    "created": False,
                }

            if proposal.recommended_order_quantity <= 0:
                logger.info(
                    "Restock proposal requires no additional order. "
                    "notification skipped. task_id=%s return_job_id=%s "
                    "proposal_id=%s",
                    task_id,
                    job_id,
                    proposal.id,
                )

                return {
                    "task_id": task_id,
                    "return_job_id": str(job_id),
                    "proposal_id": str(proposal.id),
                    "created": True,
                    "notification_published": False,
                }

            book = session.get(Book, proposal.book_id)
            book_title = book.title if book is not None else str(
                proposal.book_id
            )

        tenant_id, event = create_committed_notification_for_tenant(
            tenant_id=proposal.tenant_id,
            category=NotificationCategory.RESTOCK_ALERT,
            severity=NotificationSeverity(proposal.risk_level),
            title="반려 도서 대체 발주 추천 생성",
            message=(
                f"'{book_title}' 반려 건에 대한 대체 발주 추천이 "
                f"생성되었습니다. 추천 수량: "
                f"{proposal.recommended_order_quantity}권"
            ),
            payload={
                "order_proposal_id": str(proposal.id),
                "return_job_id": str(job_id),
                "book_id": str(proposal.book_id),
                "proposal_source": proposal.proposal_source.value,
                "recommended_order_quantity": (
                    proposal.recommended_order_quantity
                ),
                "risk_level": proposal.risk_level,
            },
        )

        publish_event_safely(
            event_name="RESTOCK_ALERT",
            publish_function=publish_tenant_notification_event,
            tenant_id=tenant_id,
            event=event,
        )

        logger.info(
            "Restock proposal created. "
            "task_id=%s return_job_id=%s proposal_id=%s",
            task_id,
            job_id,
            proposal.id,
        )

        return {
            "task_id": task_id,
            "return_job_id": str(job_id),
            "proposal_id": str(proposal.id),
            "created": True,
            "notification_published": True,
        }

    except Exception as error:
        logger.exception(
            "Restock proposal processing failed. "
            "task_id=%s return_job_id=%s error=%s",
            task_id,
            job_id,
            error,
        )

        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=error,
                countdown=RETRY_DELAY_SECONDS,
            )

        raise

@celery_app.task(
    bind=True,
    name=SAFETY_STOCK_RESTOCK_PROPOSAL_TASK_NAME,
    max_retries=MAX_RETRIES,
    default_retry_delay=RETRY_DELAY_SECONDS,
)
def process_safety_stock_restock_proposal(
    self,
    tenant_id: str,
    book_id: str,
    safety_stock_quantity: int,
) -> dict[str, Any]:
    """
    안전재고 부족 도서에 대해 Restock Agent를 실행하고,
    관리자 승인용 추천안 및 RESTOCK_ALERT를 생성한다.
    """
    task_id = self.request.id
    parsed_tenant_id = UUID(tenant_id)
    parsed_book_id = UUID(book_id)

    try:
        with Session(engine) as session:
            result = create_restock_proposal_for_safety_stock(
                session=session,
                tenant_id=parsed_tenant_id,
                book_id=parsed_book_id,
                safety_stock_quantity=safety_stock_quantity,
            )

            proposal = result.proposal

            # 배치 탐지 뒤 재고가 보충돼 Agent 호출을 생략한 경우
            if proposal is None:
                return {
                    "task_id": task_id,
                    "book_id": str(parsed_book_id),
                    "created": False,
                    "reason": "SAFETY_STOCK_ALREADY_SATISFIED",
                }

            # 이미 같은 도서의 미처리 안전재고 추천안이 있는 경우
            if not result.created:
                return {
                    "task_id": task_id,
                    "book_id": str(parsed_book_id),
                    "proposal_id": str(proposal.id),
                    "created": False,
                    "reason": "PENDING_PROPOSAL_ALREADY_EXISTS",
                }

            # Agent가 추가 발주 불필요로 판단한 경우
            if proposal.recommended_order_quantity <= 0:
                return {
                    "task_id": task_id,
                    "book_id": str(parsed_book_id),
                    "proposal_id": str(proposal.id),
                    "created": True,
                    "notification_published": False,
                }

            book = session.get(Book, proposal.book_id)
            book_title = (
                book.title
                if book is not None
                else str(proposal.book_id)
            )

        tenant_id, event = create_committed_notification_for_tenant(
            tenant_id=proposal.tenant_id,
            category=NotificationCategory.RESTOCK_ALERT,
            severity=NotificationSeverity(proposal.risk_level),
            title="안전재고 부족 발주 추천 생성",
            message=(
                f"'{book_title}'의 안전재고 부족이 감지되어 "
                f"발주 추천안이 생성되었습니다. "
                f"추천 수량: {proposal.recommended_order_quantity}권"
            ),
            payload={
                "order_proposal_id": str(proposal.id),
                "return_job_id": None,
                "book_id": str(proposal.book_id),
                "proposal_source": (
                    proposal.proposal_source.value
                ),
                "recommended_order_quantity": (
                    proposal.recommended_order_quantity
                ),
                "risk_level": proposal.risk_level,
            },
        )

        publish_event_safely(
            event_name="RESTOCK_ALERT",
            publish_function=publish_tenant_notification_event,
            tenant_id=tenant_id,
            event=event,
        )

        return {
            "task_id": task_id,
            "book_id": str(parsed_book_id),
            "proposal_id": str(proposal.id),
            "created": True,
            "notification_published": True,
        }

    except Exception as error:
        logger.exception(
            "Safety-stock restock proposal processing failed. "
            "task_id=%s book_id=%s error=%s",
            task_id,
            book_id,
            error,
        )

        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=error,
                countdown=RETRY_DELAY_SECONDS,
            )

        raise


# DB에 저장된 AI 판정 결과를 바탕으로 WMS 승인/반려 API만 실행하는 Celery 작업
# AI 검수와 WMS 연동을 분리하여 WMS 통신 실패 시 LangGraph를 다시 실행하지 않고,
# WMS 호출 단계만 최대 3회 재시도한다.
@celery_app.task(
    bind=True,
    name=WMS_TASK_NAME,
    max_retries=MAX_RETRIES,
    default_retry_delay=RETRY_DELAY_SECONDS,
)
def process_wms_action(
    self,
    return_job_id: str,
) -> dict[str, Any]:
    """
    AI 검수가 완료된 ReturnJob의 저장된 판정값을 조회하여
    WMS 승인 또는 반려 API를 호출하는 후속 Celery 작업.

    처리 흐름:
    1. ReturnJob에 저장된 AI 판정값 조회
    2. AI 판정에 따라 WMS 승인/반려 API 호출
    3. 최종 상태를 APPROVED 또는 REJECTED로 저장
    4. 최종 상태를 Redis Pub/Sub로 전달
    5. WMS 통신 오류 발생 시 이 작업만 재시도
    """
    task_id = self.request.id
    job_id = UUID(return_job_id)

    try:
        logger.info(
            "WMS action started. task_id=%s job_id=%s",
            task_id,
            job_id,
        )

        job, wms_called = process_saved_wms_result_with_lock(
            return_job_id=job_id,
            wms_task_id=task_id,
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

        publish_event_safely(
            event_name="FINAL",
            publish_function=publish_final_event,
            job=job,
            task_id=task_id,
        )

        dispatch_restock_proposal_task_safely(
            return_job=job,
        )

        logger.info(
            "WMS action completed. "
            "task_id=%s job_id=%s status=%s",
            task_id,
            job.id,
            job.status,
        )

        return {
            "task_id": task_id,
            "job_id": str(job.id),
            "status": (
                job.status.value
                if hasattr(job.status, "value")
                else str(job.status)
            ),
        }

    except WMSTaskMismatchError as error:
        logger.warning(
            "저장된 WMS Task ID와 일치하지 않는 작업을 차단했습니다. "
            "ReturnJob 상태는 변경하지 않습니다. "
            "task_id=%s job_id=%s error=%s",
            task_id,
            job_id,
            error,
        )

        return {
            "task_id": task_id,
            "job_id": str(job_id),
            "status": "SKIPPED",
            "reason": "WMS_TASK_ID_MISMATCH",
        }

    except WMSRetryableError as error:
        logger.warning(
            "Retryable WMS request failed. "
            "task_id=%s job_id=%s retry=%s/%s error=%s",
            task_id,
            job_id,
            self.request.retries,
            self.max_retries,
            error,
        )

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
            source_task=WMS_TASK_NAME,
            error=error,
            retry_count=self.request.retries,
        )
        raise

    except WMSNonRetryableError as error:
        logger.exception(
            "Non-retryable WMS request failed. "
            "task_id=%s job_id=%s error=%s",
            task_id,
            job_id,
            error,
        )

        handle_inspection_failure(
            job_id=job_id,
            task_id=task_id,
            source_task=WMS_TASK_NAME,
            error=error,
            retry_count=self.request.retries,
        )
        raise

    except Exception as error:
        logger.exception(
            "Unexpected WMS worker error. "
            "task_id=%s job_id=%s",
            task_id,
            job_id,
        )

        handle_inspection_failure(
            job_id=job_id,
            task_id=task_id,
            source_task=WMS_TASK_NAME,
            error=error,
            retry_count=self.request.retries,
        )

        raise


# celery task
@celery_app.task(
    bind=True,
    name=TASK_NAME,
    max_retries=MAX_RETRIES,          
    default_retry_delay=RETRY_DELAY_SECONDS,  
)
def process_inspection(
    self,
    return_job_id: str,
) -> dict[str, Any]:
    """
    LangGraph 기반 AI 검수 Celery 작업.

    처리 흐름:
    1. ReturnJob 조회 및 PROCESSING 상태 변경
    2. Redis Pub/Sub로 PROCESSING 상태 전달
    3. LangGraph 기반 AI 검수 실행
    4. HITL 필요 여부 확인
    5. AI 검수 결과를 DB에 저장
    6. WMS 후속 Celery Task ID를 생성하고 DB 로그에 저장
    7. WMS 승인·반려 처리를 별도 Celery Task로 등록

    WMS 통신 실패 시 이 작업을 다시 실행하지 않고,
    process_wms_action 작업에서 WMS 처리 단계만 재시도한다.
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
            inspection_task_id=task_id,
            tenant_id=tenant_id,
            book_id=book_id,
            mode=mode,
            image_paths=image_paths,
        )

        # AI 최종 판정 확인
        decision = ai_result.get("decision")

        # HITL 중단 시 WMS를 호출하지 않고 관리자 검토 대기 상태로 저장
        if decision == "HITL_REQUIRED":
            job = save_inspection_hitl_required(
                return_job_id=job_id,
                celery_task_id=task_id,
                ai_result=ai_result,
            )
            
            create_agent_hitl_alert_safely(
                job=job,
                ai_result=ai_result,
                task_id=task_id,
            )

            publish_event_safely(
                event_name="HITL_REQUIRED",
                publish_function=publish_final_event,
                job=job,
                task_id=task_id,
            )

            logger.info(
                "Inspection paused for HITL. "
                "task_id=%s job_id=%s status=%s",
                task_id,
                job.id,
                job.status,
            )

            return {
                "task_id": task_id,
                "job_id": str(job.id),
                "tenant_id": str(job.tenant_id),
                "book_id": str(job.book_id),
                "status": ReturnJobStatus.HITL_REQUIRED.value,
                "ubci_score": job.ubci_score,
            }
        
        if decision not in ["APPROVE", "REJECT"]:
            raise ValueError(
                f"지원하지 않는 AI 판정입니다: {decision}"
            )

        # 4. AI 검수 결과를 DB에 먼저 저장
        job = save_ai_inspection_result(
            return_job_id=job_id,
            celery_task_id=task_id,
            ai_result=ai_result,
        )

        # 5. WMS 호출은 별도 Celery 작업으로 분리
        wms_task_id = str(uuid4())

        try:
            # Celery 발행 전에 WMS Task ID를 DB 로그에 저장한다.
            save_wms_task_id(
                return_job_id=job.id,
                inspection_task_id=task_id,
                wms_task_id=wms_task_id,
            )

            # DB에 저장한 ID와 동일한 Task ID로 Celery 작업을 등록한다.
            celery_app.send_task(
                WMS_TASK_NAME,
                args=[str(job.id)],
                task_id=wms_task_id,
            )

        except Exception as error:
            logger.exception(
                "WMS Celery Task 등록에 실패했습니다. "
                "inspection_task_id=%s wms_task_id=%s job_id=%s",
                task_id,
                wms_task_id,
                job.id,
            )

            save_wms_dispatch_failed_safely(
                job_id=job.id,
                task_id=task_id,
                error=error,
            )

            publish_event_safely(
                event_name="WMS_DISPATCH_FAILED",
                publish_function=publish_wms_dispatch_failed_event,
                job_id=job.id,
                task_id=task_id,
                error=error,
            )

            push_dlq_safely(
                job_id=job.id,
                task_id=task_id,
                source_task=TASK_NAME,
                error=error,
                retry_count=self.request.retries,
            )

            raise WMSTaskDispatchError(
                "WMS 후속 Celery Task 등록에 실패했습니다."
            ) from error


        logger.info(
            "WMS action task queued. "
            "inspection_task_id=%s wms_task_id=%s job_id=%s",
            task_id,
            wms_task_id,
            job.id,
        )

        logger.info(
            "AI inspection completed. "
            "task_id=%s job_id=%s status=%s",
            task_id,
            job.id,
            job.status,
        )

        return {
            "task_id": task_id,
            "wms_task_id": wms_task_id,
            "job_id": str(job.id),
            "tenant_id": str(job.tenant_id),
            "book_id": str(job.book_id),
            "status": (
                job.status.value
                if hasattr(job.status, "value")
                else str(job.status)
            ),
            "ubci_score": (
                float(job.ubci_score)
                if job.ubci_score is not None
                else None
            ),
        }


    except WMSTaskDispatchError as error:
        logger.error(
            "WMS 후속 Task 등록에 실패했습니다. "
            "AI 결과는 보존하고 ReturnJob은 FAILED로 처리했습니다. "
            "task_id=%s job_id=%s error=%s",
            task_id,
            job_id,
            error,
        )

        raise

    # LangGraph, DB 저장, Celery 후속 작업 등록 등
    # 예상하지 못한 일반 오류를 최종 실패 처리
    except Exception as error:
        logger.exception(
            "Inspection processing failed. "
            "task_id=%s job_id=%s error=%s",
            task_id,
            job_id,
            error,
        )

        if self.request.retries < self.max_retries:
            logger.warning(
                "Retrying AI inspection. "
                "task_id=%s job_id=%s retry=%s/%s",
                task_id,
                job_id,
                self.request.retries,
                self.max_retries,
            )

            raise self.retry(
                exc=error,
                countdown=RETRY_DELAY_SECONDS,
            )

        handle_inspection_failure(
            job_id=job_id,
            task_id=task_id,
            source_task=TASK_NAME,
            error=error,
            retry_count=self.request.retries,
        )
        
        raise

