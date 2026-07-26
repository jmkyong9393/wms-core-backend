from uuid import uuid4

from app.core.celery_app import celery_app
from app.schemas.hitl import HITLAction

# 관리자 승인,반려 이후 실행할 WMS 후속 Celery Task 이름
WMS_TASK_NAME = "app.worker.process_wms_action"


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


# 관리자 판단 종류에 따라 적절한 후속 Celery 작업을 등록
def dispatch_hitl_followup_task(
    *,
    job_id: str,
    action: HITLAction,
    task_id: str,
) -> None:
    if action in {
        HITLAction.APPROVE_NORMAL,
        HITLAction.APPROVE_DOWNGRADE,
        HITLAction.REJECT_RETURN,
        HITLAction.REJECT_DISCARD,
    }:
        dispatch_wms_task(
            job_id=job_id,
            task_id=task_id,
        )
        return

    if action == HITLAction.RE_CHECK:
        raise ValueError(
            "RE_CHECK는 새 이미지가 등록된 후 검수 작업을 실행해야 합니다."
        )

    raise ValueError(
        f"지원하지 않는 HITL 판단입니다: {action}"
    )