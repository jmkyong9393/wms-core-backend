from typing import List, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter()


class CreateInspectionRequest(BaseModel):
    book_id: UUID
    mode: Literal["RETURN", "USED_PURCHASE"]
    image_paths: List[str]


class CreateInspectionResponse(BaseModel):
    job_id: UUID
    message: str


@router.post(
    "",
    response_model=CreateInspectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_inspection(request: CreateInspectionRequest):
    return CreateInspectionResponse(
        job_id=uuid4(),
        message="검수 파이프라인 가동 시작",
    )


@router.get("/{job_id}/stream")
def stream_inspection_status(job_id: UUID):
    def event_stream():
        yield 'data: {"status": "PROCESSING", "agent": "Vision"}\n\n'
        yield 'data: {"status": "APPROVED", "message": "정상 환불 처리"}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream")
