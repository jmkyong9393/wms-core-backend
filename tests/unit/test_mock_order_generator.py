from decimal import Decimal
from uuid import UUID

from app.models.wms import (
    Book,
    ConditionGrade,
    Order,
    OrderItem,
)
from app.services.mock_order_generator_service import (
    create_mock_outbound_order,
)


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(
        self,
        exec_results,
        books_by_id=None,
    ):
        self.exec_results = list(exec_results)
        self.books_by_id = books_by_id or {}
        self.added_items = []

    def exec(self, _statement):
        if not self.exec_results:
            raise AssertionError(
                "Unexpected database query was executed"
            )

        return FakeResult(
            self.exec_results.pop(0)
        )

    def get(self, _model, object_id):
        return self.books_by_id.get(object_id)

    def add(self, item):
        self.added_items.append(item)

    def flush(self):
        pass


def build_book(
    book_id: str,
    title: str,
    price: str,
) -> Book:
    return Book(
        id=UUID(book_id),
        title=title,
        base_price=Decimal(price),
        virtual_stock=10,
    )


def test_creates_new_stock_order_when_virtual_quantity_remains():
    book = build_book(
        "00000000-0000-4000-8000-000000000001",
        "신간 테스트 도서",
        "18000",
    )

    session = FakeSession(
        exec_results=[
            # PENDING 신간 주문 수량
            [(book.id, 4)],
            # 실제 신간 가용 수량
            [(book.id, 5)],
            # PENDING 중고 주문 건수
            [],
            # AVAILABLE 중고 LPN 수
            [],
        ],
        books_by_id={
            book.id: book,
        },
    )

    result = create_mock_outbound_order(session)

    assert result is not None
    assert result.source == "NEW_STOCK"
    assert result.book_id == book.id
    assert result.condition_grade is None
    assert result.total_price == Decimal("18000")

    assert len(session.added_items) == 2
    assert isinstance(session.added_items[0], Order)
    assert isinstance(session.added_items[1], OrderItem)
    assert session.added_items[1].condition_grade is None
    assert session.added_items[1].quantity == 1


def test_does_not_create_new_stock_order_when_pending_orders_use_all_stock():
    book = build_book(
        "00000000-0000-4000-8000-000000000002",
        "신간 재고 소진 도서",
        "18000",
    )

    session = FakeSession(
        exec_results=[
            # PENDING 신간 주문 5권
            [(book.id, 5)],
            # 실제 가용 재고도 5권
            [(book.id, 5)],
            # PENDING 중고 주문 건수
            [],
            # AVAILABLE 중고 LPN 수
            [],
        ],
        books_by_id={
            book.id: book,
        },
    )

    result = create_mock_outbound_order(session)

    assert result is None
    assert session.added_items == []


def test_creates_used_lpn_order_when_pending_count_is_lower_than_available_lpns():
    book = build_book(
        "00000000-0000-4000-8000-000000000003",
        "중고 LPN 테스트 도서",
        "12000",
    )

    session = FakeSession(
        exec_results=[
            # PENDING 신간 주문 수량
            [],
            # 실제 신간 가용 수량
            [],
            # PENDING EXCELLENT 중고 주문 1건
            [(book.id, ConditionGrade.EXCELLENT, 1)],
            # AVAILABLE EXCELLENT 중고 LPN 2개
            [(book.id, ConditionGrade.EXCELLENT, 2)],
        ],
        books_by_id={
            book.id: book,
        },
    )

    result = create_mock_outbound_order(session)

    assert result is not None
    assert result.source == "USED_LPN"
    assert result.book_id == book.id
    assert result.condition_grade == ConditionGrade.EXCELLENT

    assert len(session.added_items) == 2
    assert isinstance(session.added_items[1], OrderItem)
    assert (
        session.added_items[1].condition_grade
        == ConditionGrade.EXCELLENT
    )
    assert session.added_items[1].quantity == 1


def test_does_not_create_used_lpn_order_when_pending_orders_use_all_lpns():
    book = build_book(
        "00000000-0000-4000-8000-000000000004",
        "중고 LPN 소진 도서",
        "12000",
    )

    session = FakeSession(
        exec_results=[
            # PENDING 신간 주문 수량
            [],
            # 실제 신간 가용 수량
            [],
            # PENDING EXCELLENT 중고 주문 1건
            [(book.id, ConditionGrade.EXCELLENT, 1)],
            # AVAILABLE EXCELLENT 중고 LPN도 1개
            [(book.id, ConditionGrade.EXCELLENT, 1)],
        ],
        books_by_id={
            book.id: book,
        },
    )

    result = create_mock_outbound_order(session)

    assert result is None
    assert session.added_items == []