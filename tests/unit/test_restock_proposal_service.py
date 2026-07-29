from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.wms import (
    InboundItem,
    OrderProposalStatus,
    ReturnJobStatus,
)
from app.schemas.restock import RestockRecommendationResponse
from app.services import restock_service


class FakeResult:
    def __init__(
        self,
        *,
        first_value=None,
        one_value=None,
    ):
        self.first_value = first_value
        self.one_value = one_value

    def first(self):
        return self.first_value

    def one(self):
        return self.one_value


class FakeSession:
    def __init__(self, results, model_values):
        self.results = list(results)
        self.model_values = model_values
        self.added_items = []
        self.commit_count = 0
        self.refresh_count = 0

    def exec(self, statement):
        return self.results.pop(0)

    def get(self, model, _id):
        return self.model_values.get(model)

    def add(self, item):
        self.added_items.append(item)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _item):
        self.refresh_count += 1


def build_rejected_return_job():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000002"
        ),
        book_id=UUID("00000000-0000-4000-8000-000000000003"),
        inbound_item_id=UUID(
            "00000000-0000-4000-8000-000000000004"
        ),
        status=ReturnJobStatus.REJECTED,
        agent_logs={
            "admin_decision_code": "DMG_EXT_WET",
        },
    )


def test_creates_restock_proposal_from_rejected_job(
    monkeypatch,
):
    return_job = build_rejected_return_job()

    book = SimpleNamespace(
        id=return_job.book_id,
        isbn="9790000000001",
        title="테스트 도서",
    )
    inbound_item = SimpleNamespace(quantity=3)

    session = FakeSession(
        results=[
            # ReturnJob 조회
            FakeResult(first_value=return_job),
            # 기존 OrderProposal 조회
            FakeResult(first_value=None),
            # 최근 7일 출고 수량
            FakeResult(one_value=45),
            # 신간 가용 재고
            FakeResult(one_value=2),
            # 판매 가능 중고 LPN 수
            FakeResult(one_value=1),
            # 진행 중 AUTO_PO 수량
            FakeResult(one_value=4),
        ],
        model_values={
            restock_service.Book: book,
            InboundItem: inbound_item,
        },
    )

    captured_request = {}

    def fake_generate_restock_recommendation(request):
        captured_request["value"] = request

        return RestockRecommendationResponse(
            isbn=request.isbn,
            book_title=request.book_title,
            recommended_order_quantity=45,
            reason_summary="반려 수량을 고려한 대체 발주가 필요합니다.",
            evidence=["최근 판매량 45권", "가용 재고 3권"],
            risk_level="HIGH",
        )

    monkeypatch.setattr(
        restock_service,
        "generate_restock_recommendation",
        fake_generate_restock_recommendation,
    )

    result = (
        restock_service.create_restock_proposal_for_rejected_job(
            session=session,
            return_job_id=return_job.id,
        )
    )

    assert result.created is True
    assert len(session.added_items) == 1
    assert session.commit_count == 1
    assert session.refresh_count == 1

    request = captured_request["value"]
    assert request.isbn == "9790000000001"
    assert request.book_title == "테스트 도서"
    assert request.recent_sales_quantity == 45
    assert request.current_stock == 3
    assert request.pending_auto_po_quantity == 4
    assert request.rejected_quantity == 3
    assert request.rejection_reason_code == "DMG_EXT_WET"

    proposal = result.proposal
    assert proposal.pending_auto_po_quantity == 4
    assert proposal.return_job_id == return_job.id
    assert proposal.tenant_id == return_job.tenant_id
    assert proposal.book_id == return_job.book_id
    assert proposal.recommended_order_quantity == 45
    assert proposal.risk_level == "HIGH"


def test_returns_existing_proposal_without_agent_call(
    monkeypatch,
):
    return_job = build_rejected_return_job()
    existing_proposal = SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000010"),
    )

    session = FakeSession(
        results=[
            FakeResult(first_value=return_job),
            FakeResult(first_value=existing_proposal),
        ],
        model_values={},
    )

    def fail_if_agent_is_called(_request):
        raise AssertionError("Agent must not be called")

    monkeypatch.setattr(
        restock_service,
        "generate_restock_recommendation",
        fail_if_agent_is_called,
    )

    result = (
        restock_service.create_restock_proposal_for_rejected_job(
            session=session,
            return_job_id=return_job.id,
        )
    )

    assert result.created is False
    assert result.proposal is existing_proposal
    assert session.added_items == []
    assert session.commit_count == 0


@pytest.mark.parametrize(
    "status",
    [
        ReturnJobStatus.PENDING,
        ReturnJobStatus.PROCESSING,
        ReturnJobStatus.APPROVED,
        ReturnJobStatus.FAILED,
    ],
)
def test_rejects_non_rejected_return_job(status):
    return_job = build_rejected_return_job()
    return_job.status = status

    session = FakeSession(
        results=[
            FakeResult(first_value=return_job),
        ],
        model_values={},
    )

    with pytest.raises(
        ValueError,
        match="only be created for a rejected ReturnJob",
    ):
        restock_service.create_restock_proposal_for_rejected_job(
            session=session,
            return_job_id=return_job.id,
        )

def test_saves_not_required_status_for_zero_recommendation(
    monkeypatch,
):
    return_job = build_rejected_return_job()

    book = SimpleNamespace(
        id=return_job.book_id,
        isbn="9790000000001",
        title="재고 충분 도서",
    )
    inbound_item = SimpleNamespace(quantity=1)

    session = FakeSession(
        results=[
            # ReturnJob 조회
            FakeResult(first_value=return_job),
            # 기존 OrderProposal 조회
            FakeResult(first_value=None),
            # 최근 7일 출고 수량
            FakeResult(one_value=2),
            # 신간 가용 재고
            FakeResult(one_value=100),
            # 판매 가능 중고 LPN 수
            FakeResult(one_value=0),
            # 진행 중 AUTO_PO 수량
            FakeResult(one_value=5),
        ],
        model_values={
            restock_service.Book: book,
            InboundItem: inbound_item,
        },
    )

    def fake_generate_restock_recommendation(request):
        return RestockRecommendationResponse(
            isbn=request.isbn,
            book_title=request.book_title,
            recommended_order_quantity=0,
            reason_summary="현재 재고가 충분합니다.",
            evidence=["최근 판매량 2권", "가용 재고 100권"],
            risk_level="LOW",
        )

    monkeypatch.setattr(
        restock_service,
        "generate_restock_recommendation",
        fake_generate_restock_recommendation,
    )

    result = (
        restock_service.create_restock_proposal_for_rejected_job(
            session=session,
            return_job_id=return_job.id,
        )
    )

    assert result.created is True
    assert result.proposal.status == (
        OrderProposalStatus.NOT_REQUIRED
    )
    assert result.proposal.pending_auto_po_quantity == 5