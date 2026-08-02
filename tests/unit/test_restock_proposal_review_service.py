from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.models.wms import (
    Order,
    OrderItem,
    OrderProposalStatus,
)
from app.services.restock_proposal_service import (
    InvalidRestockProposalStateError,
    approve_restock_proposal,
    reject_restock_proposal,
)


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
    def __init__(self, results):
        self.results = list(results)
        self.added_items = []
        self.executed_statements = []
        self.flush_count = 0
        self.commit_count = 0
        self.refresh_count = 0

    def exec(self, statement):
        self.executed_statements.append(str(statement))
        return self.results.pop(0)

    def add(self, item):
        self.added_items.append(item)

    def flush(self):
        self.flush_count += 1

    def commit(self):
        self.commit_count += 1

    def refresh(self, _item):
        self.refresh_count += 1


def build_book():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000010"),
        title="대체 발주 테스트 도서",
        publisher="테스트 출판사",
        base_price=Decimal("15000"),
    )


def build_proposal(
    *,
    status=OrderProposalStatus.PENDING,
    recommended_order_quantity=4,
    pending_auto_po_quantity=7,
):
    now = datetime(2026, 7, 29, 10, 0, 0)

    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000020"),
        tenant_id=UUID(
            "00000000-0000-4000-8000-000000000001"
        ),
        book_id=UUID("00000000-0000-4000-8000-000000000010"),
        status=status,
        recommended_order_quantity=recommended_order_quantity,
        pending_auto_po_quantity=pending_auto_po_quantity,
        auto_po_order_id=None,
        reviewer_id=None,
        reviewed_at=None,
        review_comment=None,
        updated_at=now,
    )


def build_reviewer():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000030"),
    )


def test_approve_creates_auto_po_for_residual_quantity():
    proposal = build_proposal(
        recommended_order_quantity=4,
        pending_auto_po_quantity=7,
    )
    book = build_book()
    reviewer = build_reviewer()

    session = FakeSession(
        results=[
            FakeResult(first_value=(proposal, book)),
            # 도서 단위 Advisory Lock
            FakeResult(first_value=book),
            # 승인 시점 진행 AUTO_PO 수량
            FakeResult(one_value=8),
        ],
    )

    result = approve_restock_proposal(
        session=session,
        tenant_id=proposal.tenant_id,
        proposal_id=proposal.id,
        reviewer=reviewer,
        comment="대체 발주 승인",
    )

    # 추천 당시 7권 → 승인 시점 8권:
    # 추가된 1권을 추천 4권에서 제외하고 3권만 발주
    assert result.auto_po_created is True
    assert result.ordered_quantity == 3
    assert proposal.status == OrderProposalStatus.APPROVED
    assert proposal.reviewer_id == reviewer.id
    assert proposal.review_comment == "대체 발주 승인"
    assert proposal.auto_po_order_id is not None

    auto_po_order = next(
        item
        for item in session.added_items
        if isinstance(item, Order)
    )
    auto_po_item = next(
        item
        for item in session.added_items
        if isinstance(item, OrderItem)
    )

    assert auto_po_order.total_price == Decimal("45000")
    assert auto_po_item.quantity == 3
    assert auto_po_item.unit_price == Decimal("15000")
    assert auto_po_item.final_price == Decimal("45000")
    assert session.flush_count == 1
    assert any(
        "FROM books" in statement
        and "FOR UPDATE" in statement
        for statement in session.executed_statements
    )
    assert session.commit_count == 1


def test_approve_marks_not_required_when_pending_auto_po_increased():
    proposal = build_proposal(
        recommended_order_quantity=4,
        pending_auto_po_quantity=7,
    )
    book = build_book()
    reviewer = build_reviewer()

    session = FakeSession(
        results=[
            FakeResult(first_value=(proposal, book)),
            # 도서 단위 Advisory Lock
            FakeResult(first_value=book),
            # 추천 생성 이후 진행 AUTO_PO가 4권 증가
            FakeResult(one_value=11),
        ],
    )

    result = approve_restock_proposal(
        session=session,
        tenant_id=proposal.tenant_id,
        proposal_id=proposal.id,
        reviewer=reviewer,
        comment=None,
    )

    assert result.auto_po_created is False
    assert result.ordered_quantity == 0
    assert proposal.status == OrderProposalStatus.NOT_REQUIRED
    assert proposal.auto_po_order_id is None
    assert "추가 발주가 필요하지 않습니다." in (
        proposal.review_comment
    )
    assert not any(
        isinstance(item, Order)
        for item in session.added_items
    )
    assert any(
        "FROM books" in statement
        and "FOR UPDATE" in statement
        for statement in session.executed_statements
    )
    assert session.flush_count == 0
    assert session.commit_count == 1


def test_reject_marks_proposal_rejected_without_auto_po():
    proposal = build_proposal()
    book = build_book()
    reviewer = build_reviewer()

    session = FakeSession(
        results=[
            FakeResult(first_value=(proposal, book)),
        ],
    )

    result = reject_restock_proposal(
        session=session,
        tenant_id=proposal.tenant_id,
        proposal_id=proposal.id,
        reviewer=reviewer,
        comment="현재 발주가 필요하지 않습니다.",
    )

    assert result.auto_po_created is False
    assert result.ordered_quantity == 0
    assert proposal.status == OrderProposalStatus.REJECTED
    assert proposal.reviewer_id == reviewer.id
    assert proposal.review_comment == (
        "현재 발주가 필요하지 않습니다."
    )
    assert proposal.auto_po_order_id is None
    assert session.flush_count == 0
    assert session.commit_count == 1


@pytest.mark.parametrize(
    "status",
    [
        OrderProposalStatus.APPROVED,
        OrderProposalStatus.REJECTED,
        OrderProposalStatus.NOT_REQUIRED,
    ],
)
def test_review_rejects_non_pending_proposal(status):
    proposal = build_proposal(status=status)
    book = build_book()
    reviewer = build_reviewer()

    session = FakeSession(
        results=[
            FakeResult(first_value=(proposal, book)),
        ],
    )

    with pytest.raises(InvalidRestockProposalStateError):
        reject_restock_proposal(
            session=session,
            tenant_id=proposal.tenant_id,
            proposal_id=proposal.id,
            reviewer=reviewer,
            comment=None,
        )