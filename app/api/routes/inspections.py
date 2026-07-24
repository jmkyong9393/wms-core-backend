from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.dependencies.auth import get_current_user
from app.core.database import get_session
from app.models.wms import (
    ConditionGrade,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    InspectionMode,
    Location,
    ReturnJob,
    ReturnJobStatus,
    User,
)
from app.services.inspection_task_service import enqueue_inspection
from app.services.sse_ticket_service import issue_sse_ticket

router = APIRouter()


class CreateInspectionRequest(BaseModel):
    inbound_item_id: UUID
    book_id: UUID
    mode: InspectionMode
    location_barcode: str = Field(min_length=1)
    image_paths: list[str] = Field(min_length=1)


class CreateInspectionResponse(BaseModel):
    job_id: UUID
    task_id: str
    status: ReturnJobStatus
    message: str
    stream_ticket_url: str

class InspectionStatusResponse(BaseModel):
    job_id: UUID
    task_id: str | None
    status: ReturnJobStatus
    progress: int
    ubci_score: float | None
    condition_grade: ConditionGrade | None
    final_report: str | None

class StreamTicketResponse(BaseModel):
    ticket: str
    stream_url: str
    expires_in: int

# 진행률
def get_inspection_progress(
    inspection_status: ReturnJobStatus,
) -> int:
    progress_by_status = {
        ReturnJobStatus.PENDING: 20,
        ReturnJobStatus.PROCESSING: 50,
        ReturnJobStatus.HITL_REQUIRED: 70,
        ReturnJobStatus.APPROVED: 100,
        ReturnJobStatus.REJECTED: 100,
        ReturnJobStatus.FAILED: 100,
    }

    return progress_by_status.get(inspection_status, 0)

# 검수 작업 요청
@router.post(
    "",
    response_model=CreateInspectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_inspection(
    request: CreateInspectionRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CreateInspectionResponse:
    inbound_item = session.exec(
        select(InboundItem)
        .where(InboundItem.id == request.inbound_item_id)
        .with_for_update()
    ).first()
    if inbound_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbound item not found",
        )
    if inbound_item.book_id != request.book_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound item and inspection reference different books",
        )
    if inbound_item.lpn_barcode is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound item does not have an LPN barcode",
        )

    inbound_job = session.get(InboundJob, inbound_item.inbound_job_id)
    if inbound_job is None or inbound_job.status != InboundStatus.CHECKING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound job is not waiting for inspection",
        )

    expected_inbound_type = {
        InspectionMode.RETURN: InboundType.CUSTOMER_RETURN,
        InspectionMode.USED_PURCHASE: InboundType.USED_PURCHASE,
    }[request.mode]
    if inbound_job.inbound_type != expected_inbound_type:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inspection mode does not match inbound type",
        )

    existing_return_job = session.exec(
        select(ReturnJob)
        .where(ReturnJob.inbound_item_id == inbound_item.id)
        .order_by(ReturnJob.created_at.desc())
    ).first()
    if (
        existing_return_job is not None
        and existing_return_job.status != ReturnJobStatus.FAILED
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound item already has an inspection job",
        )

    location = session.exec(
        select(Location).where(Location.barcode == request.location_barcode)
    ).first()
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location barcode not found",
        )
    if not location.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Location is inactive",
        )

    return_job = ReturnJob(
        tenant_id=current_user.tenant_id,
        book_id=request.book_id,
        inbound_item_id=inbound_item.id,
        target_location_id=location.id,
        mode=request.mode,
        image_paths=request.image_paths,
        status=ReturnJobStatus.PENDING,
    )

    session.add(return_job)
    session.commit()
    session.refresh(return_job)

    try:
        task_id = enqueue_inspection(
            session=session,
            return_job=return_job,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "검수 작업을 비동기 처리 큐에 등록하지 못했습니다. "
                "잠시 후 다시 시도해 주세요."
            ),
            headers={"Retry-After": "5"},
        ) from error

    return CreateInspectionResponse(
        job_id=return_job.id,
        task_id=task_id,
        status=return_job.status,
        message="검수 파이프라인 가동 시작",
        stream_ticket_url=(
            f"/api/v1/inspections/"
            f"{return_job.id}/stream-ticket"
        ),
    )
# 개별 작업 조회 API 추가
@router.get(
    "/{job_id}",
    response_model=InspectionStatusResponse,
)
def get_inspection_status(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InspectionStatusResponse:
    statement = select(ReturnJob).where(
        ReturnJob.id == job_id,
        ReturnJob.tenant_id == current_user.tenant_id,
    )

    return_job = session.exec(statement).first()

    if return_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="검수 작업을 찾을 수 없습니다.",
        )

    return InspectionStatusResponse(
        job_id=return_job.id,
        task_id=return_job.task_id,
        status=return_job.status,
        progress=get_inspection_progress(return_job.status),
        ubci_score=return_job.ubci_score,
        condition_grade=return_job.condition_grade,
        final_report=return_job.final_report,
    )

# 티켓 발급 API
@router.post(
    "/{job_id}/stream-ticket",
    response_model=StreamTicketResponse,
)
async def create_stream_ticket(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> StreamTicketResponse:
    statement = select(ReturnJob).where(
        ReturnJob.id == job_id,
        ReturnJob.tenant_id == current_user.tenant_id,
    )

    return_job = session.exec(statement).first()

    if return_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="검수 작업을 찾을 수 없습니다.",
        )

    ticket, expires_in = await issue_sse_ticket(
        job_id=return_job.id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    return StreamTicketResponse(
        ticket=ticket,
        stream_url=(
            f"/api/v1/inspections/"
            f"{return_job.id}/stream"
            f"?ticket={ticket}"
        ),
        expires_in=expires_in,
    )
