from uuid import uuid4

from app.core.celery_app import celery_app
from app.schemas.hitl import HITLAction

# 관리자 승인,반려 이후 실행할 WMS 후속 Celery Task 이름
WMS_TASK_NAME = "app.worker.process_wms_action"

# 관리자 재검수 요청 이후 실행할 AI 검수 Celery Task 이름
INSPECTION_TASK_NAME = "app.worker.process_inspection"

# HITL 관리자 판단 이후 실행할 새 Celery Task ID 생성
def create_hitl_task_id() -> str:
    return str(uuid4())


# 관리자 APPROVE/REJECT 판단 이후 WMS 후속 작업을 Celery Queue에 등록
def dispatch_wms_task(
    *,
    job_id: str,
    task_id: str,
) -> None:
    celery_app.send_task(
        WMS_TASK_NAME,
        args=[job_id],
        task_id=task_id,
    )

# 관리자 RECALCULATE 판단 이후 AI 재검수 작업을 Celery Queue에 등록
def dispatch_recalculation_task(
    *,
    job_id: str,
    task_id: str,
) -> None:
    celery_app.send_task(
        INSPECTION_TASK_NAME,
        args=[job_id],
        task_id=task_id,
    )


# 관리자 판단 종류에 따라 적절한 후속 Celery 작업을 등록
def dispatch_hitl_followup_task(
    *,
    job_id: str,
    action: HITLAction,
    task_id: str,
) -> None:
    if action in {
        HITLAction.APPROVE,
        HITLAction.REJECT,
    }:
       dispatch_wms_task(
            job_id=job_id,
            task_id=task_id,
        )
       return

    if action == HITLAction.RECALCULATE:
        dispatch_recalculation_task(
            job_id=job_id,
            task_id=task_id,
        )
        return
    
    raise ValueError(
        f"지원하지 않는 HITL 판단입니다: {action}"
    )