from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.wms import (
    OrderProposalStatus,
    RestockProposalSource,
)
from app.services.restock_proposal_service import (
    RestockProposalNotFoundError,
    get_restock_proposal_detail,
    list_restock_proposals,
)


class FakeResult:
    def __init__(
        self,
        *,
        all_value=None,
        first_value=None,
    ):
        self.all_value = all_value or []
        self.first_value = first_value

    def all(self):
        return self.all_value

    def first(self):
        return self.first_value


class FakeSession:
    def __init__(self, results, model_values=None):
        self.results = list(results)
        self.model_values = model_values or {}

    def exec(self, _statement):
        return self.results.pop(0)

    def get(self, model, _id):
        return self.model_values.get(model)


def build_book():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000010"),
        title="Restock 테스트 도서",
        isbn="9790000000001",
        publisher="테스트 출판사",
    )


def build_proposal(
    *,
    status=OrderProposalStatus.PENDING,
    reviewer_id=None,
):
    now = datetime(2026, 7, 29, 10, 0, 0)

    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000020"),
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000001"
        ),
        book_id=UUID("00000000-0000-4000-8000-000000000010"),
        return_job_id=UUID(
            "00000000-0000-4000-8000-000000000030"
        ),
        proposal_source=RestockProposalSource.RETURN_REJECTION,
        status=status,
        recent_sales_quantity=10,
        current_stock=2,
        pending_auto_po_quantity=3,
        rejected_quantity=1,
        rejection_reason_code="DMG_EXT_WET",
        recommended_order_quantity=6,
        reason_summary="반려 수량을 고려한 대체 발주가 필요합니다.",
        evidence=["최근 판매량 10권", "실질 가용 수량 5권"],
        risk_level="HIGH",
        auto_po_order_id=None,
        reviewer_id=reviewer_id,
        reviewed_at=None,
        review_comment=None,
        created_at=now,
        updated_at=now,
    )


def test_lists_tenant_restock_proposals():
    book = build_book()
    proposal = build_proposal()

    session = FakeSession(
        results=[
            FakeResult(
                all_value=[
                    (proposal, book),
                ],
            ),
        ],
    )

    result = list_restock_proposals(
        session=session,
        tenant_id=proposal.tenant_id,
        status=OrderProposalStatus.PENDING,
    )

    assert len(result) == 1
    assert result[0].id == proposal.id
    assert result[0].book.title == "Restock 테스트 도서"
    assert result[0].status == OrderProposalStatus.PENDING
    assert result[0].pending_auto_po_quantity == 3
    assert result[0].recommended_order_quantity == 6
    assert result[0].proposal_source == (
        RestockProposalSource.RETURN_REJECTION
    )


def test_returns_restock_proposal_detail_with_reviewer():
    book = build_book()
    reviewer_id = UUID(
        "00000000-0000-4000-8000-000000000040"
    )
    proposal = build_proposal(reviewer_id=reviewer_id)
    reviewer = SimpleNamespace(
        employee_id="ADMIN-001",
    )

    session = FakeSession(
        results=[
            FakeResult(
                first_value=(proposal, book),
            ),
        ],
        model_values={
            # get() 호출 모델과 관계없이 동일 reviewer를 반환해도 충분
            object: reviewer,
        },
    )
    session.get = lambda _model, _id: reviewer

    result = get_restock_proposal_detail(
        session=session,
        tenant_id=proposal.tenant_id,
        proposal_id=proposal.id,
    )

    assert result.id == proposal.id
    assert result.book.isbn == "9790000000001"
    assert result.reviewer_id == reviewer_id
    assert result.reviewer_employee_id == "ADMIN-001"
    assert result.evidence == [
        "최근 판매량 10권",
        "실질 가용 수량 5권",
    ]


def test_raises_when_restock_proposal_is_not_found():
    session = FakeSession(
        results=[
            FakeResult(first_value=None),
        ],
    )

    with pytest.raises(RestockProposalNotFoundError):
        get_restock_proposal_detail(
            session=session,
            tenant_id=UUID(
                "00000000-0000-4000-8000-000000000001"
            ),
            proposal_id=UUID(
                "00000000-0000-4000-8000-000000000020"
            ),
        )