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

    try:
        task = celery_app.send_task(
            INSPECTION_TASK_NAME,
            args=[str(return_job.id)],
        )
    except Exception:
        return_job.status = ReturnJobStatus.FAILED
        return_job.updated_at = datetime.utcnow()

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        raise

    return_job.task_id = task.id
    return_job.updated_at = datetime.utcnow()

    session.add(return_job)
    session.commit()
    session.refresh(return_job)

    return task.id