from datetime import datetime

from sqlmodel import Session

from app.models.wms import ReturnJob
from app.worker import process_inspection

# 생성된 검수 작업을 Celery Queue에 등록하고, 발급된 task_id를 ReturnJob에 저장하는 함수
def enqueue_inspection(
    session: Session,
    return_job: ReturnJob,
) -> str:

    # DB에 저장된 ReturnJob만 Worker에 전달할 수 있다.
    if return_job.id is None:
        raise ValueError(
            "Celery 작업 등록 전에 ReturnJob이 DB에 저장되어야 합니다."
        )

    # Worker에는 검수 작업의 ID만 문자열로 전달한다.
    task = process_inspection.delay(
        str(return_job.id)
    )

    # Celery 및 Flower에서 작업을 추적할 수 있도록 저장한다.
    return_job.task_id = task.id
    return_job.updated_at = datetime.utcnow()

    session.add(return_job)
    session.commit()
    session.refresh(return_job)

    return task.id