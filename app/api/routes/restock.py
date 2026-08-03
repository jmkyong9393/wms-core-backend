from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlmodel import Session

from app.api.dependencies.auth import require_admin_or_master
from app.core.database import get_session
from app.models.wms import (
    OrderProposalStatus,
    User,
)
from app.schemas.restock import (
    RestockProposalDetailResponse,
    RestockProposalListItemResponse,
    RestockRecommendationRequest,
    RestockRecommendationResponse,
    RestockProposalReviewRequest,
    RestockProposalReviewResponse,
)
from app.services.restock_proposal_service import (
    InvalidRestockProposalStateError,
    RestockProposalNotFoundError,
    approve_restock_proposal,
    get_restock_proposal_detail,
    list_restock_proposals,
    reject_restock_proposal,
)
from app.services.restock_service import (
    generate_restock_recommendation,
)
from app.services.location_assignment_service import (
    NoAvailableLocationError,
)

router = APIRouter()


@router.post(
    "/mock-recommendation",
    response_model=RestockRecommendationResponse,
    summary="자동 발주 추천 Agent 수동 호출(개발 확인용)",
)
def create_mock_restock_recommendation(
    request: RestockRecommendationRequest,
    current_admin: User = Depends(
        require_admin_or_master,
    ),
) -> RestockRecommendationResponse:
    """
    Restock Agent를 수동으로 호출해 추천 결과만 확인하는 개발용 API.

    실제 업무 흐름에서는 최종 반려된 검수 작업을 기준으로
    Worker가 Restock 추천안을 생성하고 OrderProposal에 저장한다.

    이 API는 OrderProposal 저장, 관리자 알림, AUTO_PO 생성은 수행하지 않는다.
    """
    try:
        return generate_restock_recommendation(
            request,
        )

    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "자동 발주 추천 Agent 처리 중 오류가 발생했습니다."
            ),
        ) from exc


@router.get(
    "/proposals",
    response_model=list[RestockProposalListItemResponse],
    summary="Restock 추천안 목록 조회",
)
def get_restock_proposal_list(
    proposal_status: OrderProposalStatus | None = Query(
        default=None,
        alias="status",
        description=(
            "추천안 상태 필터"
            "(PENDING, APPROVED, REJECTED, NOT_REQUIRED)"
        ),
    ),
    current_admin: User = Depends(
        require_admin_or_master,
    ),
    session: Session = Depends(get_session),
) -> list[RestockProposalListItemResponse]:
    """
    현재 관리자 테넌트의 Restock 추천안을 최신 생성 순으로 조회한다.

    상태를 지정하면 해당 상태의 추천안만 반환한다.
    """
    return list_restock_proposals(
        session=session,
        tenant_id=current_admin.tenant_id,
        status=proposal_status,
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=RestockProposalDetailResponse,
    summary="Restock 추천안 상세 조회",
    responses={
        404: {
            "description": "추천안을 찾을 수 없거나 다른 테넌트의 추천안",
        },
    },
)
def get_restock_proposal(
    proposal_id: UUID,
    current_admin: User = Depends(
        require_admin_or_master,
    ),
    session: Session = Depends(get_session),
) -> RestockProposalDetailResponse:
    """
    관리자 검토에 필요한 추천안의 Agent 입력값, 근거, 처리 이력을 조회한다.
    """
    try:
        return get_restock_proposal_detail(
            session=session,
            tenant_id=current_admin.tenant_id,
            proposal_id=proposal_id,
        )

    except RestockProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restock 추천안을 찾을 수 없습니다.",
        ) from exc

@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=RestockProposalReviewResponse,
    summary="Restock 추천안 승인 및 AUTO_PO 생성",
    responses={
        404: {"description": "추천안을 찾을 수 없음"},
        409: {
            "description": (
                "이미 처리된 추천안이거나 추가 발주가 필요 없거나, "
                "신간 적치 가능 로케이션이 없음"
            )
        },
    },
)
def approve_restock_proposal_api(
    proposal_id: UUID,
    request: RestockProposalReviewRequest,
    current_admin: User = Depends(
        require_admin_or_master,
    ),
    session: Session = Depends(get_session),
) -> RestockProposalReviewResponse:
    """
    PENDING 추천안을 승인하고 잔여 수량이 있으면 AUTO_PO 주문을 생성한다.

    승인 시점에 새 AUTO_PO가 추가된 경우 해당 수량을 제외해,
    이미 충분하면 NOT_REQUIRED 상태로 처리한다.
    """
    try:
        result = approve_restock_proposal(
            session=session,
            tenant_id=current_admin.tenant_id,
            proposal_id=proposal_id,
            reviewer=current_admin,
            comment=request.comment,
        )

        return RestockProposalReviewResponse(
            proposal_id=result.proposal.id,
            status=result.proposal.status,
            auto_po_order_id=result.proposal.auto_po_order_id,
            reviewed_at=result.proposal.reviewed_at,
            message=result.message,
        )

    except RestockProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restock 추천안을 찾을 수 없습니다.",
        ) from exc

    except InvalidRestockProposalStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except NoAvailableLocationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "신간 재고를 적치할 수 있는 A Zone 로케이션이 없습니다. "
                f"{exc}"
            ),
        ) from exc


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=RestockProposalReviewResponse,
    summary="Restock 추천안 반려",
    responses={
        404: {"description": "추천안을 찾을 수 없음"},
        409: {"description": "이미 처리되었거나 반려할 수 없는 추천안"},
    },
)
def reject_restock_proposal_api(
    proposal_id: UUID,
    request: RestockProposalReviewRequest,
    current_admin: User = Depends(
        require_admin_or_master,
    ),
    session: Session = Depends(get_session),
) -> RestockProposalReviewResponse:
    """
    PENDING 추천안을 반려 처리한다.

    반려된 추천안은 AUTO_PO 주문을 생성하지 않는다.
    """
    try:
        result = reject_restock_proposal(
            session=session,
            tenant_id=current_admin.tenant_id,
            proposal_id=proposal_id,
            reviewer=current_admin,
            comment=request.comment,
        )

        return RestockProposalReviewResponse(
            proposal_id=result.proposal.id,
            status=result.proposal.status,
            auto_po_order_id=result.proposal.auto_po_order_id,
            reviewed_at=result.proposal.reviewed_at,
            message=result.message,
        )

    except RestockProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restock 추천안을 찾을 수 없습니다.",
        ) from exc

    except InvalidRestockProposalStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc