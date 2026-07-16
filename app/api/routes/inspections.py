from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.core.database import get_session
from app.models.wms import (
    InspectionMode,
    ReturnJob,
    ReturnJobStatus,
)
from app.services.inspection_task_service import enqueue_inspection

router = APIRouter()


class CreateInspectionRequest(BaseModel):
    book_id: UUID
    mode: InspectionMode
    image_paths: list[str] = Field(min_length=1)


class CreateInspectionResponse(BaseModel):
    job_id: UUID
    task_id: str
    status: ReturnJobStatus
    message: str
    stream_url: str

# 검수 작업 요청
@router.post(
    "",
    response_model=CreateInspectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_inspection(
    request: CreateInspectionRequest,
    session: Session = Depends(get_session),
) -> CreateInspectionResponse:       
        #1. 검수 요청을 ReturnJob으로 저장
        return_job = ReturnJob(
            book_id=request.book_id,
            mode=request.mode,
            image_paths=request.image_paths,
            status=ReturnJobStatus.PENDING,
        )

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        #2. 생성된 ReturnJob을 Celery Queue에 등록
        task_id = enqueue_inspection(
            session=session,
            return_job=return_job,
        )

        # 3. Worker 완료를 기다리지 않고 즉시 응답
        return CreateInspectionResponse(
            job_id=return_job.id,
            task_id=task_id,
            status = return_job.status,
            message="검수 파이프라인 가동 시작",
            stream_url=(
                f"/api/v1/inspections/"
                f"{return_job.id}/stream"
            ),
        )


