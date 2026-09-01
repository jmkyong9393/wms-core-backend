from uuid import uuid4
from datetime import datetime

from sqlmodel import Session

from app.core.celery_app import celery_app
from app.models.wms import (
    ReturnJob,
    ReturnJobStatus,)

INSPECTION_TASK_NAME = "app.worker.process_inspection"

# 검수 작업을 Celery Queue에 등록하고 task_id를 저장한다.
def enqueue_inspection(
    session: Session,
    return_job: ReturnJob,
) -> str:
    if return_job.id is None:
        raise ValueError(
            "Celery 작업 등록 전에 ReturnJob이 DB에 저장되어야 합니다."
        )

    task_id = str(uuid4())

    # Worker가 먼저 실행되더라도 동일한 task_id를 확인할 수 있도록
    # Celery 발행 전에 DB에 task_id를 저장한다.
    return_job.task_id = task_id
    return_job.updated_at = datetime.utcnow()

    updated_logs = dict(return_job.agent_logs or {})
    updated_logs["inspection_task_id"] = task_id
    return_job.agent_logs = updated_logs

    session.add(return_job)
    session.commit()
    session.refresh(return_job)

    try:
        celery_app.send_task(
            INSPECTION_TASK_NAME,
            args=[str(return_job.id)],
            task_id=task_id,
        )
    except Exception as error:
        session.rollback()
        session.refresh(return_job)

        # Celery Queue에 등록되지 않은 작업이므로 실행 대기 상태로 남기지 않는다.
        return_job.task_id = None
        return_job.status = ReturnJobStatus.FAILED
        return_job.final_report = (
            "Celery 작업 등록에 실패했습니다. 다시 시도해 주세요."
        )

        # 큐 등록 실패 원인을 추적할 수 있도록 작업 로그에 저장
        updated_logs = dict(return_job.agent_logs or {})

        if updated_logs.get("inspection_task_id") == task_id:
            updated_logs.pop("inspection_task_id", None)

        updated_logs["inspection_dispatch_error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "failed_at": datetime.utcnow().isoformat(),
        }

        return_job.agent_logs = updated_logs

        return_job.updated_at = datetime.utcnow()

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        raise

    return task_id

