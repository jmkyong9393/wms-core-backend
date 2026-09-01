import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.ai.restock_agent import restock_agent
from app.domains.restock.schemas.restock import (
    RestockRecommendationRequest,
    RestockRecommendationResponse,
)
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
    RestockProposalSource,
    ReturnJob,
    ReturnJobStatus,
    UsedInventoryStatus,
)

logger = logging.getLogger(__name__)

RECENT_SALES_DAYS = 7
IN_PROGRESS_AUTO_PO_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.PICKING,
    OrderStatus.SHIPPED,
)

RESTOCK_GENERATION_LOG_KEY = "restock_generation"

RESTOCK_GENERATION_GENERATING = "GENERATING"
RESTOCK_GENERATION_RESPONSE_SAVED = "RESPONSE_SAVED"
RESTOCK_GENERATION_COMPLETED = "COMPLETED"
RESTOCK_GENERATION_FAILED = "FAILED"

SAFETY_STOCK_REASON_CODE = "SAFETY_STOCK_SHORTAGE"


@dataclass(frozen=True)
class RestockProposalCreationResult:
    proposal: OrderProposal | None
    created: bool
    generation_in_progress: bool = False


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
    최종 반려된 검수 작업을 기준으로 Restock Agent 추천안을 생성한다.

    Agent 호출 전후 상태와 응답을 ReturnJob.agent_logs에 저장한다.
    따라서 Worker가 Agent 호출 직후 종료되더라도, 응답이 이미 저장된 경우에는
    재시도 시 OpenAI를 다시 호출하지 않고 OrderProposal 저장을 이어서 처리한다.
    """
    return_job = session.exec(select(ReturnJob).where(ReturnJob.id == return_job_id).with_for_update()).first()

    if return_job is None:
        raise ValueError(f"Restock proposal target ReturnJob was not found: {return_job_id}")

    if return_job.status != ReturnJobStatus.REJECTED:
        raise ValueError(
            f"Restock proposal can only be created for a rejected ReturnJob. current_status={return_job.status}"
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
        raise ValueError(f"Book for ReturnJob was not found: {return_job.book_id}")

    generation_log = _get_restock_generation_log(return_job)
    saved_request = generation_log.get("request")
    saved_response = generation_log.get("response")

    # 이전 실행에서 Agent 응답까지 저장된 경우:
    # OpenAI를 다시 호출하지 않고 추천안 DB 저장만 이어서 수행한다.
    if saved_response is not None:
        if not isinstance(saved_request, dict):
            raise RuntimeError("Saved Restock Agent response exists without request snapshot.")

        request = RestockRecommendationRequest.model_validate(saved_request)
        recommendation = RestockRecommendationResponse.model_validate(saved_response)

    # 다른 Worker가 이미 Agent 호출을 시작했고 아직 응답을 남기지 않은 경우:
    # 중복 OpenAI 호출을 막고 현재 작업은 조용히 종료한다.
    elif generation_log.get("status") == RESTOCK_GENERATION_GENERATING:
        logger.warning(
            "Restock Agent generation is already in progress. return_job_id=%s",
            return_job.id,
        )

        return RestockProposalCreationResult(
            proposal=None,
            created=False,
            generation_in_progress=True,
        )

    else:
        # Agent 통신 실패 후 재시도하는 경우에도,
        # 최초 Agent 호출에 사용한 입력 스냅샷을 그대로 재사용한다.
        if generation_log.get("status") == RESTOCK_GENERATION_FAILED and isinstance(saved_request, dict):
            request = RestockRecommendationRequest.model_validate(saved_request)
        else:
            request = _build_restock_recommendation_request(
                session=session,
                return_job=return_job,
                book=book,
            )

        # OpenAI 호출 전에 GENERATING 상태와 입력 스냅샷을 먼저 확정한다.
        # 여기서 commit해야 Worker 강제 종료 시에도 상태가 남는다.
        _save_restock_generation_log(
            return_job=return_job,
            status=RESTOCK_GENERATION_GENERATING,
            request=request.model_dump(mode="json"),
        )
        session.add(return_job)
        session.commit()

        try:
            recommendation = generate_restock_recommendation(request)

        except Exception as error:
            # 일반적인 API 통신 실패는 FAILED로 기록하고 Celery 재시도를 허용한다.
            _save_restock_generation_log(
                return_job=return_job,
                status=RESTOCK_GENERATION_FAILED,
                request=request.model_dump(mode="json"),
                last_error=str(error),
            )
            session.add(return_job)
            session.commit()
            raise

        # Agent 응답을 추천안 생성보다 먼저 확정 저장한다.
        # 이후 proposal 저장 단계에서 죽어도 재시도 시 OpenAI 재호출이 없다.
        _save_restock_generation_log(
            return_job=return_job,
            status=RESTOCK_GENERATION_RESPONSE_SAVED,
            request=request.model_dump(mode="json"),
            response=recommendation.model_dump(mode="json"),
        )
        session.add(return_job)
        session.commit()

    proposal = OrderProposal(
        tenant_id=return_job.tenant_id,
        book_id=book.id,
        return_job_id=return_job.id,
        proposal_source=RestockProposalSource.RETURN_REJECTION,
        recent_sales_quantity=request.recent_sales_quantity,
        current_stock=request.current_stock,
        pending_auto_po_quantity=(request.pending_auto_po_quantity),
        rejected_quantity=request.rejected_quantity,
        rejection_reason_code=request.rejection_reason_code,
        recommended_order_quantity=(recommendation.recommended_order_quantity),
        reason_summary=recommendation.reason_summary,
        evidence=recommendation.evidence,
        risk_level=recommendation.risk_level,
        status=(
            OrderProposalStatus.NOT_REQUIRED
            if recommendation.recommended_order_quantity <= 0
            else OrderProposalStatus.PENDING
        ),
    )

    _save_restock_generation_log(
        return_job=return_job,
        status=RESTOCK_GENERATION_COMPLETED,
        request=request.model_dump(mode="json"),
        response=recommendation.model_dump(mode="json"),
    )

    # 추천안 생성과 COMPLETED 상태 변경은 하나의 commit으로 처리한다.
    session.add(proposal)
    session.add(return_job)
    session.commit()
    session.refresh(proposal)

    logger.info(
        "Restock proposal created. proposal_id=%s return_job_id=%s recommended_order_quantity=%s",
        proposal.id,
        return_job.id,
        proposal.recommended_order_quantity,
    )

    return RestockProposalCreationResult(
        proposal=proposal,
        created=True,
    )


def create_restock_proposal_for_safety_stock(
    session: Session,
    tenant_id: UUID,
    book_id: UUID,
    safety_stock_quantity: int,
) -> RestockProposalCreationResult:
    """
    안전재고 부족 도서를 대상으로 같은 Restock Agent를 실행하고
    관리자 승인용 OrderProposal을 생성한다.

    동일 도서에 미처리 안전재고 추천안이 이미 있으면 기존 추천안을 반환해
    CronJob 재실행으로 인한 중복 추천안을 막는다.
    """
    if safety_stock_quantity < 1:
        raise ValueError("Safety stock quantity must be at least 1.")

    # 승인 처리와 배치 추천 생성이 같은 도서에서 동시에 실행될 때를 직렬화한다.
    book = session.exec(select(Book).where(Book.id == book_id).with_for_update()).first()

    if book is None:
        raise ValueError(f"Safety-stock restock target book was not found: {book_id}")

    existing_proposal = session.exec(
        select(OrderProposal)
        .where(
            OrderProposal.tenant_id == tenant_id,
            OrderProposal.book_id == book.id,
            OrderProposal.proposal_source == RestockProposalSource.SAFETY_STOCK,
            OrderProposal.status == OrderProposalStatus.PENDING,
        )
        .with_for_update()
    ).first()

    if existing_proposal is not None:
        return RestockProposalCreationResult(
            proposal=existing_proposal,
            created=False,
        )

    current_stock = get_current_available_stock(
        session=session,
        book_id=book.id,
    )
    pending_auto_po_quantity = get_pending_auto_po_quantity(
        session=session,
        book_id=book.id,
    )

    effective_stock = current_stock + pending_auto_po_quantity

    # 배치가 후보를 찾은 뒤 재고가 늘어난 경우 Agent 호출 자체를 생략한다.
    if effective_stock >= safety_stock_quantity:
        return RestockProposalCreationResult(
            proposal=None,
            created=False,
        )

    request = RestockRecommendationRequest(
        proposal_source=RestockProposalSource.SAFETY_STOCK,
        isbn=book.isbn or str(book.id),
        book_title=book.title,
        recent_sales_quantity=_get_recent_sales_quantity(
            session=session,
            book_id=book.id,
        ),
        current_stock=current_stock,
        pending_auto_po_quantity=pending_auto_po_quantity,
        rejected_quantity=0,
        rejection_reason_code=SAFETY_STOCK_REASON_CODE,
        safety_stock_quantity=safety_stock_quantity,
    )

    recommendation = generate_restock_recommendation(
        request,
    )

    proposal = OrderProposal(
        tenant_id=tenant_id,
        book_id=book.id,
        return_job_id=None,
        proposal_source=RestockProposalSource.SAFETY_STOCK,
        recent_sales_quantity=request.recent_sales_quantity,
        current_stock=request.current_stock,
        pending_auto_po_quantity=(request.pending_auto_po_quantity),
        rejected_quantity=0,
        rejection_reason_code=SAFETY_STOCK_REASON_CODE,
        recommended_order_quantity=(recommendation.recommended_order_quantity),
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
        "Safety-stock restock proposal created. proposal_id=%s book_id=%s recommended_order_quantity=%s",
        proposal.id,
        book.id,
        proposal.recommended_order_quantity,
    )

    return RestockProposalCreationResult(
        proposal=proposal,
        created=True,
    )


def _build_restock_recommendation_request(
    session: Session,
    return_job: ReturnJob,
    book: Book,
) -> RestockRecommendationRequest:
    """반려 검수 작업과 현재 재고·판매 데이터를 Agent 입력 스냅샷으로 만든다."""
    return RestockRecommendationRequest(
        isbn=book.isbn or str(book.id),
        book_title=book.title,
        recent_sales_quantity=_get_recent_sales_quantity(
            session=session,
            book_id=book.id,
        ),
        current_stock=get_current_available_stock(
            session=session,
            book_id=book.id,
        ),
        pending_auto_po_quantity=get_pending_auto_po_quantity(
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


def _get_restock_generation_log(
    return_job: ReturnJob,
) -> dict[str, Any]:
    """기존 검수 로그를 보존한 채 Restock 생성 로그만 조회한다."""
    agent_logs = return_job.agent_logs or {}
    generation_log = agent_logs.get(RESTOCK_GENERATION_LOG_KEY)

    return dict(generation_log) if isinstance(generation_log, dict) else {}


def _save_restock_generation_log(
    return_job: ReturnJob,
    status: str,
    request: dict[str, Any],
    response: dict[str, Any] | None = None,
    last_error: str | None = None,
) -> None:
    """
    Restock Agent 실행 상태를 ReturnJob.agent_logs에 저장한다.

    기존 reason_code, admin_decision_code 등 검수 로그는 유지한다.
    """
    agent_logs = dict(return_job.agent_logs or {})

    generation_log: dict[str, Any] = {
        "status": status,
        "request": request,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if response is not None:
        generation_log["response"] = response

    if last_error is not None:
        generation_log["last_error"] = last_error

    agent_logs[RESTOCK_GENERATION_LOG_KEY] = generation_log
    return_job.agent_logs = agent_logs


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
            Order.created_at >= (datetime.utcnow() - timedelta(days=RECENT_SALES_DAYS)),
        )
    ).one()

    return int(result or 0)


def get_current_available_stock(
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
                func.sum(Inventory.quantity - Inventory.reserved_quantity),
                0,
            )
        ).where(
            Inventory.book_id == book_id,
        )
    ).one()

    available_used_lpn_count = session.exec(
        select(func.count(InventoryUsedItem.id)).where(
            InventoryUsedItem.book_id == book_id,
            InventoryUsedItem.status == UsedInventoryStatus.AVAILABLE,
        )
    ).one()

    return int(new_stock or 0) + int(available_used_lpn_count or 0)


def get_pending_auto_po_quantity(
    session: Session,
    book_id: UUID,
) -> int:
    """
    이미 생성되어 입고를 기다리는 AUTO_PO 수량을 조회한다.

    기존 auto_po_batch.py와 같은 진행 상태를 사용해,
    Agent가 중복 대체 발주를 추천하지 않도록 한다.
    """
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
            Order.type == OrderType.AUTO_PO,
            col(Order.status).in_(IN_PROGRESS_AUTO_PO_STATUSES),
        )
    ).one()

    return int(result or 0)


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

    return agent_logs.get("admin_decision_code") or agent_logs.get("reason_code") or "UNKNOWN"
