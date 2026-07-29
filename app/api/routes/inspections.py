from uuid import UUID
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.dependencies.auth import (
    get_current_user,
    require_admin,
)
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

from app.schemas.hitl import (
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
from app.services.inspection_image_service import (
    InspectionImageValidationError,
    normalize_cloudfront_image_urls,
)
from app.services.inspection_task_service import enqueue_inspection
from app.services.sse_ticket_service import issue_sse_ticket
from app.services.redis_pubsub import publish_return_job_event


from app.core.exceptions import HITLTaskDispatchException


router = APIRouter()

logger = logging.getLogger(__name__)


class CreateInspectionRequest(BaseModel):
    inbound_item_id: UUID
    book_id: UUID
    mode: InspectionMode
    location_barcode: str = Field(min_length=1)
    image_paths: list[str] = Field(
        min_length=1,
        description=(
            "S3 업로드 완료 후 조회 가능한 CloudFront 이미지 URL 목록. "
            "도서 한 권의 대표·측면·내지·결함 이미지를 촬영 순서대로 전달합니다."
        ),
        examples=[
            [
                (
                    "https://d3j61tpuly7r0p.cloudfront.net/"
                    "uploads/cover.jpg"
                ),
                (
                    "https://d3j61tpuly7r0p.cloudfront.net/"
                    "uploads/inside-1.jpg"
                ),
            ]
        ],
    )


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

class RecheckInspectionRequest(BaseModel):
    image_paths: list[str] = Field(
        min_length=1,
        description=(
            "S3 재업로드 완료 후 조회 가능한 CloudFront 이미지 URL 목록. "
            "기존 검수 이미지를 대체할 대표·측면·내지·결함 이미지를 "
            "촬영 순서대로 전달합니다."
        ),
        examples=[
            [
                (
                    "https://d3j61tpuly7r0p.cloudfront.net/"
                    "uploads/recheck-cover.jpg"
                ),
                (
                    "https://d3j61tpuly7r0p.cloudfront.net/"
                    "uploads/recheck-defect-1.jpg"
                ),
            ]
        ],
    )

class RecheckInspectionResponse(BaseModel):
    job_id: UUID
    task_id: str
    status: ReturnJobStatus
    message: str
    stream_ticket_url: str



# 진행률
def get_inspection_progress(
    inspection_status: ReturnJobStatus,
) -> int:
    progress_by_status = {
        ReturnJobStatus.PENDING: 20,
        ReturnJobStatus.PROCESSING: 50,
        ReturnJobStatus.HITL_REQUIRED: 70,
        ReturnJobStatus.RECHECK_REQUIRED: 70,
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
    try:
        normalized_image_paths = normalize_cloudfront_image_urls(
            request.image_paths
        )
    except InspectionImageValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

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
        image_paths=normalized_image_paths,
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
    agent_logs = return_job.agent_logs or {}
    wms_task_id = agent_logs.get("wms_task_id")

    current_task_id = (
        str(wms_task_id)
        if wms_task_id
        else return_job.task_id
    )

    return InspectionStatusResponse(
        job_id=return_job.id,
        task_id=current_task_id,
        status=return_job.status,
        progress=get_inspection_progress(return_job.status),
        ubci_score=return_job.ubci_score,
        condition_grade=return_job.condition_grade,
        final_report=return_job.final_report,
    )

# 재촬영 이미지 등록 API
@router.post(
    "/{job_id}/recheck",
    response_model=RecheckInspectionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def recheck_inspection(
    job_id: UUID,
    request: RecheckInspectionRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> RecheckInspectionResponse:
    try:
        normalized_image_paths = normalize_cloudfront_image_urls(
            request.image_paths
        )
    except InspectionImageValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error

    statement = (
        select(ReturnJob)
        .where(
            ReturnJob.id == job_id,
            ReturnJob.tenant_id == current_user.tenant_id,
        )
        .with_for_update()
    )

    return_job = session.exec(statement).first()

    if return_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="재촬영 대상 검수 작업을 찾을 수 없습니다.",
        )

    if return_job.status != ReturnJobStatus.RECHECK_REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "재촬영 이미지를 등록할 수 있는 상태가 아닙니다. "
                f"현재 상태: {return_job.status.value}"
            ),
        )

    # 기존 이미지를 재촬영 이미지로 교체
    return_job.image_paths = normalized_image_paths

    # 새 Celery 작업을 등록하기 전 대기 상태로 변경
    return_job.status = ReturnJobStatus.PENDING
    return_job.task_id = None

    updated_logs = dict(return_job.agent_logs or {})

    hitl_logs = dict(updated_logs.get("hitl") or {})
    hitl_logs.update(
        {
            "waiting_for_new_images": False,
            "recheck_images_uploaded": True,
        }
    )

    updated_logs["hitl"] = hitl_logs
    return_job.agent_logs = updated_logs

    session.add(return_job)
    session.commit()
    session.refresh(return_job)

    try:
        task_id = enqueue_inspection(
            session=session,
            return_job=return_job,
        )

    except Exception as error:
        session.rollback()

        # Queue 등록 실패 시 재촬영 대기 상태로 복구
        restore_statement = (
            select(ReturnJob)
            .where(
                ReturnJob.id == job_id,
                ReturnJob.tenant_id == current_user.tenant_id,
            )
            .with_for_update()
        )

        failed_job = session.exec(restore_statement).first()

        if failed_job is not None:
            failed_job.status = ReturnJobStatus.RECHECK_REQUIRED
            failed_job.task_id = None

            failed_logs = dict(failed_job.agent_logs or {})
            failed_hitl_logs = dict(
                failed_logs.get("hitl") or {}
            )

            failed_hitl_logs.update(
                {
                    "waiting_for_new_images": True,
                    "recheck_images_uploaded": True,
                    "recheck_dispatch_failed": True,
                    "recheck_dispatch_error": str(error),
                }
            )

            failed_logs["hitl"] = failed_hitl_logs
            failed_job.agent_logs = failed_logs

            session.add(failed_job)
            session.commit()

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "재검수 작업을 비동기 처리 큐에 등록하지 못했습니다. "
                "잠시 후 다시 시도해 주세요."
            ),
            headers={"Retry-After": "5"},
        ) from error

    try:
        publish_return_job_event(
            job_id=str(return_job.id),
            event={
                "job_id": str(return_job.id),
                "task_id": task_id,
                "status": ReturnJobStatus.PENDING.value,
                "progress": 20,
                "message": "재촬영 이미지가 등록되어 재검수를 대기 중입니다.",
            },
        )
    except Exception:
        logger.exception(
            "재검수 PENDING SSE 이벤트 발행 실패: job_id=%s",
            return_job.id,
        )

    return RecheckInspectionResponse(
        job_id=return_job.id,
        task_id=task_id,
        status=return_job.status,
        message=(
            "재촬영 이미지가 등록되었으며 "
            "AI 재검수를 시작합니다."
        ),
        stream_ticket_url=(
            f"/api/v1/inspections/"
            f"{return_job.id}/stream-ticket"
        ),
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
    requires_wms_task = request.action in {
        HITLAction.APPROVE_NORMAL,
        HITLAction.APPROVE_DOWNGRADE,
        HITLAction.REJECT_RETURN,
        HITLAction.REJECT_DISCARD,
    }

    task_id = (
        create_hitl_task_id()
        if requires_wms_task
        else None
    )

    # 관리자 판단 내용과 후속 Task ID를 ReturnJob에 저장
    return_job = save_hitl_decision(
        session=session,
        job_id=job_id,
        current_admin=current_admin,
        action=request.action,
        reviewer_reason_code=request.reviewer_reason_code,
        target_grade=request.target_grade,
        comment=request.comment,
        task_id=task_id,
    )

    if request.action == HITLAction.RE_CHECK:
        try:
            publish_return_job_event(
                job_id=str(return_job.id),
                event={
                    "job_id": str(return_job.id),
                    "task_id": None,
                    "status": ReturnJobStatus.RECHECK_REQUIRED.value,
                    "progress": 70,
                    "message": "재촬영이 필요한 검수 작업입니다.",
                },
            )
        except Exception:
            logger.exception(
                "RECHECK_REQUIRED SSE 이벤트 발행 실패: job_id=%s",
                return_job.id,
            )

    
    # 관리자 판단 종류에 따라 WMS 처리 또는 AI 재검수 Task 등록
    if requires_wms_task:
        try:
            dispatch_hitl_followup_task(
                job_id=str(return_job.id),
                action=request.action,
                task_id=task_id,
            )

        except Exception as error:
            session.rollback()

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
            session.rollback()

            logger.exception(
                "HITL 임시 백업 데이터 제거 실패: "
                "job_id=%s, task_id=%s",
                return_job.id,
                task_id,
            )
    
    # 관리자 판단 종류에 맞는 응답 메시지 생성
    if request.action == HITLAction.APPROVE_NORMAL:
        message = (
            "정상 승인 결과가 저장되었으며 "
            "기존 AI 판정 등급을 기준으로 "
            "WMS 입고 처리를 시작합니다."
        )

    elif request.action == HITLAction.APPROVE_DOWNGRADE:
        message = (
            f"하향 승인 결과가 저장되었으며 "
            f"{request.target_grade.value}등급으로 "
            "WMS 입고 처리를 시작합니다."
        )

    elif request.action == HITLAction.REJECT_RETURN:
        message = (
            "고객 반송 결정이 저장되었으며 "
            "WMS 반려 처리를 시작합니다."
        )

    elif request.action == HITLAction.REJECT_DISCARD:
        message = (
            "자체 폐기 결정이 저장되었으며 "
            "WMS 반려 처리를 시작합니다."
        )

    else:
        message = (
            "재촬영 요청이 저장되었습니다. "
            "새 이미지가 등록되면 검수를 다시 시작합니다."
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
