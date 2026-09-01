import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.domains.dev.demo_inventory_service import (
    DEMO_OUTBOUND_BOOK_ISBN,
)
from app.models.wms import (
    Book,
    ConditionGrade,
    Inventory,
    InventoryUsedItem,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    UsedInventoryStatus,
)

MockOrderSource = Literal["NEW_STOCK", "USED_LPN"]


@dataclass(frozen=True)
class MockOrderCandidate:
    book: Book
    source: MockOrderSource
    condition_grade: ConditionGrade | None = None
    available_quantity: int = 1


@dataclass(frozen=True)
class MockOrderGenerationResult:
    """생성된 Mock 주문의 핵심 정보."""

    order_id: UUID
    order_item_id: UUID
    source: MockOrderSource
    book_id: UUID
    condition_grade: ConditionGrade | None
    total_price: Decimal


# 가상 가용 재고가 있는 데모 도서를 대상으로 PENDING 주문 한 건을 생성한다.
def create_mock_outbound_order(
    session: Session,
    target_isbn: str | None = None,
) -> MockOrderGenerationResult | None:
    """
    신간 또는 중고 EXCELLENT LPN 중 가용한 대상을 선택해 주문을 생성한다.

    재고 예약은 피킹 지시서 생성 단계에서 수행한다.
    이 함수는 commit하지 않으며 호출자가 트랜잭션을 관리한다.
    """
    new_stock_candidate = _find_new_stock_candidate(
        session,
        target_isbn=target_isbn,
    )
    used_lpn_candidate = _find_used_lpn_candidate(
        session,
        target_isbn=target_isbn,
    )

    candidate = _select_candidate(
        new_stock_candidate,
        used_lpn_candidate,
    )
    if candidate is None:
        return None

    order_quantity = (
        random.randint(
            1,
            min(3, candidate.available_quantity),
        )
        if candidate.source == "NEW_STOCK"
        else 1
    )

    total_price = candidate.book.base_price * order_quantity

    order = Order(
        customer_id=uuid4(),
        customer_name=f"Mock B2B Customer {uuid4().hex[:8]}",
        type=OrderType.B2B_ORDER,
        total_price=total_price,
        status=OrderStatus.PENDING,
        logistics_center="SEOUL_DC",
    )
    session.add(order)
    session.flush()

    order_item = OrderItem(
        order_id=order.id,
        book_id=candidate.book.id,
        # 실제 피킹 위치는 피킹 지시서 생성 시 Allocation으로 결정한다.
        location_id=None,
        condition_grade=candidate.condition_grade,
        quantity=order_quantity,
        unit_price=candidate.book.base_price,
        final_price=total_price,
    )
    session.add(order_item)
    session.flush()

    return MockOrderGenerationResult(
        order_id=order.id,
        order_item_id=order_item.id,
        source=candidate.source,
        book_id=candidate.book.id,
        condition_grade=candidate.condition_grade,
        total_price=order.total_price,
    )


# 데모 도서의 신간 가상 가용 재고가 있는 경우 주문 후보를 반환한다.
def _find_new_stock_candidate(
    session: Session,
    target_isbn: str | None = None,
) -> MockOrderCandidate | None:
    pending_quantities = _get_pending_new_stock_quantities(
        session,
    )

    statement = (
        select(
            Inventory.book_id,
            func.sum(Inventory.quantity - Inventory.reserved_quantity).label("physical_available_quantity"),
        )
        .join(Book, Inventory.book_id == Book.id)
        .where(
            Book.base_price > 0,
            col(Inventory.discount_rate).is_not(None),
            col(Inventory.sale_price).is_not(None),
        )
    )

    if target_isbn is not None:
        statement = statement.where(Book.isbn == target_isbn)
    else:
        statement = statement.where(Book.isbn != DEMO_OUTBOUND_BOOK_ISBN)

    inventory_rows = session.exec(statement.group_by(Inventory.book_id).order_by(Inventory.book_id)).all()

    candidates: list[MockOrderCandidate] = []

    for book_id, physical_available_quantity in inventory_rows:
        physical_available = int(physical_available_quantity or 0)
        pending_quantity = pending_quantities.get(
            book_id,
            0,
        )

        available_quantity = physical_available - pending_quantity

        if available_quantity <= 0:
            continue

        book = session.get(Book, book_id)
        if book is not None:
            candidates.append(
                MockOrderCandidate(
                    book=book,
                    source="NEW_STOCK",
                    available_quantity=available_quantity,
                )
            )

    if not candidates:
        return None

    return random.choice(candidates)


# 데모 도서의 AVAILABLE EXCELLENT LPN이 있는 경우 주문 후보를 반환한다.
def _find_used_lpn_candidate(
    session: Session,
    target_isbn: str | None = None,
) -> MockOrderCandidate | None:
    pending_counts = _get_pending_used_lpn_order_counts(
        session,
    )

    statement = (
        select(
            InventoryUsedItem.book_id,
            InventoryUsedItem.condition_grade,
            func.count(InventoryUsedItem.id).label("available_lpn_count"),
        )
        .join(
            Book,
            InventoryUsedItem.book_id == Book.id,
        )
        .where(
            InventoryUsedItem.status == UsedInventoryStatus.AVAILABLE,
            InventoryUsedItem.condition_grade == ConditionGrade.EXCELLENT,
            col(InventoryUsedItem.discount_rate).is_not(None),
            col(InventoryUsedItem.sale_price).is_not(None),
            Book.base_price > 0,
        )
    )

    if target_isbn is not None:
        statement = statement.where(Book.isbn == target_isbn)
    else:
        statement = statement.where(Book.isbn != DEMO_OUTBOUND_BOOK_ISBN)

    used_lpn_rows = session.exec(
        statement.group_by(
            InventoryUsedItem.book_id,
            InventoryUsedItem.condition_grade,
        ).order_by(
            InventoryUsedItem.book_id,
            InventoryUsedItem.condition_grade,
        )
    ).all()

    candidates: list[MockOrderCandidate] = []

    for book_id, condition_grade, available_lpn_count in used_lpn_rows:
        pending_count = pending_counts.get(
            (book_id, condition_grade),
            0,
        )

        available_quantity = int(available_lpn_count) - pending_count

        if available_quantity <= 0:
            continue

        book = session.get(Book, book_id)
        if book is not None:
            candidates.append(
                MockOrderCandidate(
                    book=book,
                    source="USED_LPN",
                    condition_grade=condition_grade,
                    available_quantity=available_quantity,
                )
            )

    if not candidates:
        return None

    return random.choice(candidates)


# 아직 피킹되지 않은 신간 PENDING 주문 수량을 도서별로 합산한다.
def _get_pending_new_stock_quantities(
    session: Session,
) -> dict[UUID, int]:
    rows = session.exec(
        select(
            OrderItem.book_id,
            func.sum(OrderItem.quantity).label("pending_quantity"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.PENDING,
            OrderItem.condition_grade.is_(None),
        )
        .group_by(OrderItem.book_id)
    ).all()

    return {book_id: int(pending_quantity or 0) for book_id, pending_quantity in rows}


# 아직 피킹되지 않은 중고 PENDING 주문 수를 도서·등급별로 합산한다.
def _get_pending_used_lpn_order_counts(
    session: Session,
) -> dict[tuple[UUID, ConditionGrade], int]:
    rows = session.exec(
        select(
            OrderItem.book_id,
            OrderItem.condition_grade,
            func.count(OrderItem.id).label("pending_order_count"),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.PENDING,
            col(OrderItem.condition_grade).is_not(None),
        )
        .group_by(
            OrderItem.book_id,
            OrderItem.condition_grade,
        )
    ).all()

    return {
        (book_id, condition_grade): int(pending_order_count or 0)
        for book_id, condition_grade, pending_order_count in rows
        if condition_grade is not None
    }


# 신간과 중고 모두 가능하면 무작위 선택하고, 하나만 가능하면 해당 후보를 선택한다.
def _select_candidate(
    new_stock_candidate: MockOrderCandidate | None,
    used_lpn_candidate: MockOrderCandidate | None,
) -> MockOrderCandidate | None:
    candidates = [
        candidate
        for candidate in (
            new_stock_candidate,
            used_lpn_candidate,
        )
        if candidate is not None
    ]

    if not candidates:
        return None

    return random.choice(candidates)
