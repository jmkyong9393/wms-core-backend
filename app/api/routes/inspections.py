from uuid import UUID
import logging

from fastapi import APIRouter, Depends,HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.dependencies.auth import (
    get_current_user,
    require_admin,
)
from app.core.database import get_session
from app.models.wms import (
    InspectionMode,
    ReturnJob,
    ReturnJobStatus,
    User,
)

from app.schemas.hitl import(
    HITLAction,
    HITLDecisionRequest,
    HITLDecisionResponse,
)

from app.services.hitl_service import (
    clear_hitl_dispatch_backup,
    restore_hitl_after_dispatch_failure,
    save_hitl_decision
) 
from app.services.hitl_task_service import (
    create_hitl_task_id,
    dispatch_hitl_followup_task,
)
from app.services.inspection_task_service import enqueue_inspection
from app.services.sse_ticket_service import issue_sse_ticket



from app.core.exceptions import HITLTaskDispatchException


router = APIRouter()

logger = logging.getLogger(__name__)


class CreateInspectionRequest(BaseModel):
    book_id: UUID
    mode: InspectionMode
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
    ubci_score: int | None
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
        #1. 검수 요청을 ReturnJob으로 저장
        return_job = ReturnJob(
            tenant_id=current_user.tenant_id,
            book_id=request.book_id,
            mode=request.mode,
            image_paths=request.image_paths,
            status=ReturnJobStatus.PENDING,
        )

        session.add(return_job)
        session.commit()
        session.refresh(return_job)

        #2. 생성된 ReturnJob을 Celery Queue에 등록
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
            
        
        # 3. Worker 완료를 기다리지 않고 즉시 응답
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
        final_report=return_job.final_report,
    )


# ADMIN이 HITL_REQUIRED 검수 작업에 최종 판단을 내리는 API
@router.post(
        "/{job_id}/hitl",
        response_model=HITLDecisionResponse,
        status_code= status.HTTP_202_ACCEPTED,
)
def resolve_hitl_inspection(
    job_id: UUID,
    request: HITLDecisionRequest,
    current_admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HITLDecisionResponse:
    
    # 관리자 판단 이후 실행할 WMS 처리 또는 AI 재검수 Task ID 생성
    task_id = create_hitl_task_id()

    # 관리자 판단 내용과 후속 Task ID를 ReturnJob에 저장
    return_job = save_hitl_decision(
        session=session,
        job_id=job_id,
        current_admin=current_admin,
        action=request.action,
        reviewer_reason_code=request.reviewer_reason_code,
        comment=request.comment,
        task_id=task_id,
    )

    
    # 관리자 판단 종류에 따라 WMS 처리 또는 AI 재검수 Task 등록
    try:
        dispatch_hitl_followup_task(
            job_id=str(return_job.id),
            action=request.action,
            task_id=task_id,
        )

    except Exception as error:
        session.rollback()

        # Celery 작업 자체가 등록되지 않은 경우에만 HITL_REQUIRED 상태로 복구
        restore_hitl_after_dispatch_failure(
            session=session,
            job_id=return_job.id,
            tenant_id=current_admin.tenant_id,
            failed_task_id=task_id,
            error=error,
        )

        raise HITLTaskDispatchException() from error


    # Celery 작업 등록 성공 후 임시 복구 백업 제거
    try:
        return_job = clear_hitl_dispatch_backup(
            session=session,
            job_id=return_job.id,
            tenant_id=current_admin.tenant_id,
        )

    except Exception:
        # 실패한 DB 트랜잭션을 정리해 세션을 정상 상태로 복구
        session.rollback()

        # 후속 Task는 이미 등록되었으므로 API 처리는 계속 진행
        logger.exception(
            "HITL 임시 백업 데이터 제거 실패: job_id=%s, task_id=%s",
            return_job.id,
            task_id,
        )
    
    # 관리자 판단 종류에 맞는 응답 메시지 생성
    if request.action == HITLAction.APPROVE:
        message = (
            "관리자 승인 결과가 저장되었으며 "
            "WMS 승인 처리를 시작합니다."
        )

    elif request.action == HITLAction.REJECT:
        message = (
            "관리자 반려 결과가 저장되었으며 "
            "WMS 반려 처리를 시작합니다."
        )

    else:
        message = (
            "관리자 재검수 요청이 저장되었으며 "
            "AI 검수를 다시 시작합니다."
        )

    # 관리자 판단 접수 결과 반환
    return HITLDecisionResponse(
        job_id=return_job.id,
        action=request.action,
        status=return_job.status,
        task_id=task_id,
        message=message,
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