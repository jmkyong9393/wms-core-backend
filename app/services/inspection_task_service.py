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

    session.add(return_job)
    session.commit()
    session.refresh(return_job)

    try:
        celery_app.send_task(
            INSPECTION_TASK_NAME,
            args=[str(return_job.id)],
            task_id=task_id,
        )
    except Exception:
        session.refresh(return_job)

        # 이미 Worker가 실행 중이거나 완료된 경우 상태를 덮어쓰지 않는다.
        if return_job.status == ReturnJobStatus.PENDING:
            return_job.status = ReturnJobStatus.FAILED
            return_job.final_report = (
                "검수 작업을 비동기 처리 큐에 등록하지 못했습니다."
            )
            return_job.updated_at = datetime.utcnow()

            session.add(return_job)
            session.commit()
            session.refresh(return_job)

        raise

    return task_id