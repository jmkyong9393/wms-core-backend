from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import engine
from app.models.wms import ReturnJob
from app.worker import process_inspection

router = APIRouter()

# Request body 모델
class ReturnInspectionRequest(BaseModel):
    order_id: UUID
    book_id: UUID
    image_url: str

# 반품 도서 이미지 접수 -> ReturnJob 생성 -> Celery 비동기 작업 등록 api
@router.post("/upload", status_code = status.HTTP_202_ACCEPTED,)
def upload_return_image(request: ReturnInspectionRequest):
    with Session(engine) as session:

        # 1. ReturnJob 생성
        return_job = ReturnJob(
            order_id = request.order_id,
            book_id = request.book_id,
            image_url = request.image_url,
            status = "PENDING",
        )

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        # 2. Celery Task 생성
        task = process_inspection.delay(
            str(return_job.id)
        )

        # 3. Celery task_id 저장
        return_job.task_id = task.id
        return_job.updated_at = datetime.utcnow()

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        # 4. Worker 완료를 기다리지 않고 작업 접수 결과를 즉시 반환
        return {
            "return_job_id" : str(return_job.id),
            "task_id" : task.id,
            "status" : return_job.status,
            "stream_url" : f"/api/stream/pubsub/returns/{return_job.id}",
            "fallback_stream_url": f"/api/stream/returns/{return_job.id}",
        }

# ReturnJob 조회 및 상태 반환
@router.get("/{return_job_id}")
def get_return_status(return_job_id : UUID):
    with Session(engine) as session:

        return_job = session.exec(
            select(ReturnJob)
            .where(ReturnJob.id == return_job_id)
        ).first()

        if return_job is None:
            raise HTTPException(
                status_code=404,
                detail="ReturnJob not found"
            )
        
        return {
            "task_id": return_job.task_id,
            "return_job_id" : str(return_job.id),
            "status": return_job.status,
            "ubci_score": return_job.ubci_score,
            "final_report": return_job.final_report,
        }
