from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.wms import (
    Book,
    Order,
    OrderItem,
    OrderProposal,
    OrderProposalStatus,
    OrderStatus,
    OrderType,
    User,
)
from app.schemas.restock import (
    RestockProposalBookResponse,
    RestockProposalDetailResponse,
    RestockProposalListItemResponse,
)
from app.services.restock_service import (
    get_pending_auto_po_quantity,
)


class RestockProposalNotFoundError(LookupError):
    """요청한 테넌트에서 Restock 추천안을 찾을 수 없을 때 발생한다."""

class InvalidRestockProposalStateError(ValueError):
    """현재 상태에서 승인 또는 반려할 수 없는 추천안일 때 발생한다."""


@dataclass(frozen=True)
class RestockProposalReviewResult:
    """추천안 승인 또는 반려 처리 결과."""

    proposal: OrderProposal
    auto_po_created: bool
    ordered_quantity: int
    message: str


def list_restock_proposals(
    session: Session,
    tenant_id: UUID,
    status: OrderProposalStatus | None = None,
) -> list[RestockProposalListItemResponse]:
    """
    테넌트별 Restock 추천안 목록을 최신 생성 순으로 조회한다.

    다른 테넌트의 추천안은 조회되지 않도록 tenant_id 조건을 항상 적용한다.
    """
    statement = (
        select(OrderProposal, Book)
        .join(Book, OrderProposal.book_id == Book.id)
        .where(OrderProposal.tenant_id == tenant_id)
        .order_by(OrderProposal.created_at.desc())
    )

    if status is not None:
        statement = statement.where(
            OrderProposal.status == status,
        )

    rows = session.exec(statement).all()

    return [
        _build_list_item_response(
            proposal=proposal,
            book=book,
        )
        for proposal, book in rows
    ]


def get_restock_proposal_detail(
    session: Session,
    tenant_id: UUID,
    proposal_id: UUID,
) -> RestockProposalDetailResponse:
    """
    테넌트 소속 관리자에게 Restock 추천안 상세 정보를 반환한다.

    추천안의 Agent 판단 근거와 관리자 검토 이력까지 함께 조회한다.
    """
    row = session.exec(
        select(OrderProposal, Book)
        .join(Book, OrderProposal.book_id == Book.id)
        .where(
            OrderProposal.id == proposal_id,
            OrderProposal.tenant_id == tenant_id,
        )
    ).first()

    if row is None:
        raise RestockProposalNotFoundError(
            f"Restock proposal was not found: {proposal_id}"
        )

    proposal, book = row

    reviewer_employee_id = None

    if proposal.reviewer_id is not None:
        reviewer = session.get(User, proposal.reviewer_id)

        if reviewer is not None:
            reviewer_employee_id = reviewer.employee_id

    return RestockProposalDetailResponse(
        id=proposal.id,
        book=_build_book_response(book),
        return_job_id=proposal.return_job_id,
        status=proposal.status,
        recent_sales_quantity=proposal.recent_sales_quantity,
        current_stock=proposal.current_stock,
        pending_auto_po_quantity=(
            proposal.pending_auto_po_quantity
        ),
        rejected_quantity=proposal.rejected_quantity,
        rejection_reason_code=proposal.rejection_reason_code,
        recommended_order_quantity=(
            proposal.recommended_order_quantity
        ),
        reason_summary=proposal.reason_summary,
        evidence=list(proposal.evidence or []),
        risk_level=proposal.risk_level,
        auto_po_order_id=proposal.auto_po_order_id,
        reviewer_id=proposal.reviewer_id,
        reviewer_employee_id=reviewer_employee_id,
        reviewed_at=proposal.reviewed_at,
        review_comment=proposal.review_comment,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


def _build_list_item_response(
    proposal: OrderProposal,
    book: Book,
) -> RestockProposalListItemResponse:
    """목록 화면에 필요한 추천안 요약 응답을 만든다."""
    return RestockProposalListItemResponse(
        id=proposal.id,
        book=_build_book_response(book),
        status=proposal.status,
        recommended_order_quantity=(
            proposal.recommended_order_quantity
        ),
        risk_level=proposal.risk_level,
        recent_sales_quantity=proposal.recent_sales_quantity,
        current_stock=proposal.current_stock,
        pending_auto_po_quantity=(
            proposal.pending_auto_po_quantity
        ),
        rejected_quantity=proposal.rejected_quantity,
        created_at=proposal.created_at,
        reviewed_at=proposal.reviewed_at,
    )


def _build_book_response(
    book: Book,
) -> RestockProposalBookResponse:
    """추천안 응답에 포함할 도서 기본 정보를 만든다."""
    return RestockProposalBookResponse(
        id=book.id,
        title=book.title,
        isbn=book.isbn,
        publisher=book.publisher,
    )

def approve_restock_proposal(
    *,
    session: Session,
    tenant_id: UUID,
    proposal_id: UUID,
    reviewer: User,
    comment: str | None,
) -> RestockProposalReviewResult:
    """
    PENDING 추천안을 승인하고 필요한 잔여 수량만 AUTO_PO로 생성한다.

    추천안 생성 이후 진행 중 AUTO_PO가 늘어났다면 증가분만큼
    추천 수량에서 제외해 중복 발주를 방지한다.
    """
    proposal, book = _get_restock_proposal_for_update(
        session=session,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
    )
    _validate_pending_proposal(proposal)

    _lock_auto_po_approval_book(
        session=session,
        book_id=proposal.book_id,
    )

    current_pending_auto_po_quantity = (
        get_pending_auto_po_quantity(
            session=session,
            book_id=proposal.book_id,
        )
    )

    additional_pending_quantity = max(
        0,
        current_pending_auto_po_quantity
        - proposal.pending_auto_po_quantity,
    )

    order_quantity = max(
        0,
        proposal.recommended_order_quantity
        - additional_pending_quantity,
    )

    reviewed_at = datetime.utcnow()

    proposal.reviewer_id = reviewer.id
    proposal.reviewed_at = reviewed_at
    proposal.review_comment = comment
    proposal.updated_at = reviewed_at

    if order_quantity == 0:
        proposal.status = OrderProposalStatus.NOT_REQUIRED

        if proposal.review_comment is None:
            proposal.review_comment = (
                "승인 시점에 진행 중인 AUTO_PO 수량이 증가하여 "
                "추가 발주가 필요하지 않습니다."
            )

        session.add(proposal)
        session.commit()
        session.refresh(proposal)

        return RestockProposalReviewResult(
            proposal=proposal,
            auto_po_created=False,
            ordered_quantity=0,
            message=(
                "진행 중 AUTO_PO 수량을 반영한 결과 "
                "추가 발주가 필요하지 않습니다."
            ),
        )

    auto_po_order = Order(
        customer_name=book.publisher or "UNKNOWN_PUBLISHER",
        type=OrderType.AUTO_PO,
        total_price=book.base_price * order_quantity,
        status=OrderStatus.PENDING,
        logistics_center="CENTER_A",
    )
    session.add(auto_po_order)
    session.flush()

    auto_po_order_item = OrderItem(
        order_id=auto_po_order.id,
        book_id=book.id,
        quantity=order_quantity,
        unit_price=book.base_price,
        final_price=book.base_price * order_quantity,
    )
    session.add(auto_po_order_item)

    proposal.status = OrderProposalStatus.APPROVED
    proposal.auto_po_order_id = auto_po_order.id

    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    return RestockProposalReviewResult(
        proposal=proposal,
        auto_po_created=True,
        ordered_quantity=order_quantity,
        message=(
            f"{order_quantity}권의 AUTO_PO 주문이 생성되었습니다."
        ),
    )


def reject_restock_proposal(
    *,
    session: Session,
    tenant_id: UUID,
    proposal_id: UUID,
    reviewer: User,
    comment: str | None,
) -> RestockProposalReviewResult:
    """
    PENDING 추천안을 관리자 반려 상태로 변경한다.

    반려된 추천안은 AUTO_PO 주문을 생성하지 않는다.
    """
    proposal, _book = _get_restock_proposal_for_update(
        session=session,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
    )
    _validate_pending_proposal(proposal)

    reviewed_at = datetime.utcnow()

    proposal.status = OrderProposalStatus.REJECTED
    proposal.reviewer_id = reviewer.id
    proposal.reviewed_at = reviewed_at
    proposal.review_comment = comment
    proposal.updated_at = reviewed_at

    session.add(proposal)
    session.commit()
    session.refresh(proposal)

    return RestockProposalReviewResult(
        proposal=proposal,
        auto_po_created=False,
        ordered_quantity=0,
        message="Restock 추천안을 반려했습니다.",
    )

def _lock_auto_po_approval_book(
    session: Session,
    book_id: UUID,
) -> None:
    """
    동일 도서의 AUTO_PO 승인 처리를 직렬화한다.

    서로 다른 추천안이라도 같은 도서를 동시에 승인하면
    진행 중 AUTO_PO 수량을 중복으로 계산할 수 있으므로,
    PostgreSQL 트랜잭션 Advisory Lock을 사용한다.
    """
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:book_id, 0)"
            ")"
        ).bindparams(book_id=str(book_id))
    )

def _get_restock_proposal_for_update(
    *,
    session: Session,
    tenant_id: UUID,
    proposal_id: UUID,
) -> tuple[OrderProposal, Book]:
    """승인·반려 동시 요청을 막기 위해 추천안을 잠금 조회한다."""
    row = session.exec(
        select(OrderProposal, Book)
        .join(Book, OrderProposal.book_id == Book.id)
        .where(
            OrderProposal.id == proposal_id,
            OrderProposal.tenant_id == tenant_id,
        )
        .with_for_update()
    ).first()

    if row is None:
        raise RestockProposalNotFoundError(
            f"Restock proposal was not found: {proposal_id}"
        )

    return row


def _validate_pending_proposal(
    proposal: OrderProposal,
) -> None:
    """승인·반려는 아직 검토되지 않은 PENDING 추천안에서만 허용한다."""
    if proposal.status != OrderProposalStatus.PENDING:
        raise InvalidRestockProposalStateError(
            "Restock proposal is not pending. "
            f"current_status={proposal.status}"
        )