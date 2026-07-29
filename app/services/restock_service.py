import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.ai.restock_agent import restock_agent
from app.models.wms import (
    Book,
    InboundItem,
    Inventory,
    InventoryUsedItem,
    Order,
    OrderItem,
    OrderProposal,
    OrderProposalStatus,
    OrderStatus,
    OrderType,
    ReturnJob,
    ReturnJobStatus,
    UsedInventoryStatus,
)
from app.schemas.restock import (
    RestockRecommendationRequest,
    RestockRecommendationResponse,
)

logger = logging.getLogger(__name__)

RECENT_SALES_DAYS = 7


@dataclass(frozen=True)
class RestockProposalCreationResult:
    proposal: OrderProposal
    created: bool


def generate_restock_recommendation(
    request: RestockRecommendationRequest,
) -> RestockRecommendationResponse:
    """입력 데이터를 Restock Agent에 전달해 추천 결과를 반환한다."""
    return restock_agent(request)


def create_restock_proposal_for_rejected_job(
    session: Session,
    return_job_id: UUID,
) -> RestockProposalCreationResult:
    """
    최종 반려된 검수 작업을 기준으로 Restock Agent 추천안을 생성·저장한다.

    동일 return_job_id의 추천안이 이미 있으면 기존 데이터를 반환한다.
    """
    return_job = session.exec(
        select(ReturnJob)
        .where(ReturnJob.id == return_job_id)
        .with_for_update()
    ).first()

    if return_job is None:
        raise ValueError(
            f"Restock proposal target ReturnJob was not found: {return_job_id}"
        )

    if return_job.status != ReturnJobStatus.REJECTED:
        raise ValueError(
            "Restock proposal can only be created for a rejected ReturnJob. "
            f"current_status={return_job.status}"
        )

    existing_proposal = session.exec(
        select(OrderProposal).where(
            OrderProposal.return_job_id == return_job.id,
        )
    ).first()

    if existing_proposal is not None:
        return RestockProposalCreationResult(
            proposal=existing_proposal,
            created=False,
        )

    book = session.get(Book, return_job.book_id)
    if book is None:
        raise ValueError(
            f"Book for ReturnJob was not found: {return_job.book_id}"
        )

    request = RestockRecommendationRequest(
        isbn=book.isbn or str(book.id),
        book_title=book.title,
        recent_sales_quantity=_get_recent_sales_quantity(
            session=session,
            book_id=book.id,
        ),
        current_stock=_get_current_available_stock(
            session=session,
            book_id=book.id,
        ),
        rejected_quantity=_get_rejected_quantity(
            session=session,
            return_job=return_job,
        ),
        rejection_reason_code=_get_rejection_reason_code(
            return_job=return_job,
        ),
    )

    recommendation = generate_restock_recommendation(request)

    proposal = OrderProposal(
        tenant_id=return_job.tenant_id,
        book_id=book.id,
        return_job_id=return_job.id,
        recent_sales_quantity=request.recent_sales_quantity,
        current_stock=request.current_stock,
        rejected_quantity=request.rejected_quantity,
        rejection_reason_code=request.rejection_reason_code,
        recommended_order_quantity=(
            recommendation.recommended_order_quantity
        ),
        reason_summary=recommendation.reason_summary,
        evidence=recommendation.evidence,
        risk_level=recommendation.risk_level,
        status=(
            OrderProposalStatus.NOT_REQUIRED
            if recommendation.recommended_order_quantity <= 0
            else OrderProposalStatus.PENDING
        ),
    )

    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    logger.info(
        "Restock proposal created. proposal_id=%s return_job_id=%s "
        "recommended_order_quantity=%s",
        proposal.id,
        return_job.id,
        proposal.recommended_order_quantity,
    )

    return RestockProposalCreationResult(
        proposal=proposal,
        created=True,
    )


def _get_recent_sales_quantity(
    session: Session,
    book_id: UUID,
) -> int:
    """최근 7일간 실제 출고 완료된 B2B 주문 수량을 조회한다."""
    result = session.exec(
        select(
            func.coalesce(
                func.sum(OrderItem.quantity),
                0,
            )
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            OrderItem.book_id == book_id,
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.SHIPPED,
            Order.created_at >= (
                datetime.utcnow()
                - timedelta(days=RECENT_SALES_DAYS)
            ),
        )
    ).one()

    return int(result or 0)


def _get_current_available_stock(
    session: Session,
    book_id: UUID,
) -> int:
    """
    현재 판매 가능한 재고를 조회한다.

    신간은 실제 수량에서 피킹 예약 수량을 제외하고,
    중고는 AVAILABLE 상태의 LPN 단품만 합산한다.
    """
    new_stock = session.exec(
        select(
            func.coalesce(
                func.sum(
                    Inventory.quantity
                    - Inventory.reserved_quantity
                ),
                0,
            )
        ).where(
            Inventory.book_id == book_id,
        )
    ).one()

    available_used_lpn_count = session.exec(
        select(func.count(InventoryUsedItem.id)).where(
            InventoryUsedItem.book_id == book_id,
            InventoryUsedItem.status
            == UsedInventoryStatus.AVAILABLE,
        )
    ).one()

    return int(new_stock or 0) + int(
        available_used_lpn_count or 0
    )


def _get_rejected_quantity(
    session: Session,
    return_job: ReturnJob,
) -> int:
    """반려된 입고 품목 수량을 반환한다. 단품 연결이 없으면 1권으로 본다."""
    if return_job.inbound_item_id is None:
        return 1

    inbound_item = session.get(
        InboundItem,
        return_job.inbound_item_id,
    )

    if inbound_item is None:
        return 1

    return inbound_item.quantity


def _get_rejection_reason_code(
    return_job: ReturnJob,
) -> str:
    """관리자 HITL 사유를 우선 사용하고, 없으면 AI 사유 코드를 사용한다."""
    agent_logs = return_job.agent_logs or {}

    return (
        agent_logs.get("admin_decision_code")
        or agent_logs.get("reason_code")
        or "UNKNOWN"
    )