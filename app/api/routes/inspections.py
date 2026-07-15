from typing import Literal
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.core.database import engine
from app.models.wms import ReturnJob, ReturnJobStatus
from app.services.inspection_task_service import enqueue_inspection

router = APIRouter()


class CreateInspectionRequest(BaseModel):
    book_id: UUID
    mode: Literal["RETURN", "USED_PURCHASE"]
    image_paths: list[str] = Field(min_length=1)


class CreateInspectionResponse(BaseModel):
    job_id: UUID
    task_id: str
    status: str
    message: str
    stream_url: str


@router.post(
    "",
    response_model=CreateInspectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_inspection(
    request: CreateInspectionRequest,
) -> CreateInspectionResponse:
    with Session(engine) as session:

        # 1. WMS Core 영역:
        # 검수 요청을 ReturnJob으로 저장
        return_job = ReturnJob(
            book_id=request.book_id,
            mode=request.mode,
            image_urls=request.image_paths,
            status=ReturnJobStatus.PENDING,
        )

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        # 2. BE-2 오케스트레이션 영역:
        # 생성된 ReturnJob을 Celery Queue에 등록
        task_id = enqueue_inspection(
            session=session,
            return_job=return_job,
        )

        # 3. Worker 완료를 기다리지 않고 즉시 응답
        return CreateInspectionResponse(
            job_id=return_job.id,
            task_id=task_id,
            status=(
                return_job.status.value
                if hasattr(return_job.status, "value")
                else str(return_job.status)
            ),
            message="검수 파이프라인 가동 시작",
            stream_url=(
                f"/api/v1/inspections/"
                f"{return_job.id}/stream"
            ),
        )


