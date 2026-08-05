from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.wms import (
    Book,
    ConditionGrade,
    Inventory,
    InventoryUsedItem,
    Location,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    StandardSize,
    UsedInventoryStatus,
    BookCategory,
)


# 데모 주문 생성기가 사용할 단일 도서 마스터
DEMO_OUTBOUND_BOOK_ISBN = "9790000000001"
DEMO_OUTBOUND_BOOK_TITLE = "Demo Outbound Book"

# 신간·중고 피킹 위치를 구분하기 위한 데모 로케이션
DEMO_NEW_STOCK_LOCATION_BARCODE = "DEMO-A-2-1"
DEMO_USED_LPN_LOCATION_BARCODE = "DEMO-B-1-2"

# 가상 가용 재고가 기준 이하일 때 자동 보충한다.
MIN_NEW_STOCK_VIRTUAL_AVAILABLE = 20
MIN_USED_LPN_VIRTUAL_AVAILABLE = 10

NEW_STOCK_REPLENISH_QUANTITY = 50
USED_LPN_REPLENISH_QUANTITY = 20


@dataclass(frozen=True)
class DemoInventoryEnsureResult:
    """데모 재고 확보 결과를 호출자에게 전달한다."""

    demo_book: Book
    new_inventory: Inventory
    new_location: Location
    used_location: Location
    added_new_stock_quantity: int
    added_used_lpn_quantity: int


# 데모 주문용 신간 재고와 중고 EXCELLENT LPN을 필요할 때 보충한다.
def ensure_demo_outbound_inventory(
    session: Session,
) -> DemoInventoryEnsureResult:
    """
    같은 도서 마스터에 신간 묶음 재고와 중고 단품 재고를 준비한다.

    이 함수는 commit하지 않는다.
    API 또는 배치 실행부가 주문 생성과 함께 하나의 트랜잭션으로 commit한다.
    """
    demo_book = _get_or_create_demo_book(session)

    new_location = _get_or_create_location(
        session=session,
        barcode=DEMO_NEW_STOCK_LOCATION_BARCODE,
        zone="A",
        rack="2",
        shelf="1",
    )
    new_inventory = _get_or_create_inventory(
        session=session,
        book_id=demo_book.id,
        location_id=new_location.id,
    )

    if new_inventory.discount_rate is None:
        new_inventory.discount_rate = Decimal("0.00")

    if new_inventory.sale_price is None:
        new_inventory.sale_price = demo_book.base_price

    session.add(new_inventory)

    used_location = _get_or_create_location(
        session=session,
        barcode=DEMO_USED_LPN_LOCATION_BARCODE,
        zone="B",
        rack="1",
        shelf="2",
    )

    # 신간 가상 가용 수량이 부족하면 묶음 재고를 보충한다.
    added_new_stock_quantity = 0
    if _get_new_stock_virtual_available_quantity(
        session=session,
        book_id=demo_book.id,
        inventory=new_inventory,
    ) <= MIN_NEW_STOCK_VIRTUAL_AVAILABLE:
        added_new_stock_quantity = NEW_STOCK_REPLENISH_QUANTITY

        new_inventory.quantity += added_new_stock_quantity
        demo_book.virtual_stock = (
            (demo_book.virtual_stock or 0)
            + added_new_stock_quantity
        )

        session.add(new_inventory)
        session.add(demo_book)

    # 중고 가상 가용 LPN이 부족하면 새 EXCELLENT LPN을 추가한다.
    added_used_lpn_quantity = 0
    if _get_used_lpn_virtual_available_quantity(
        session=session,
        book_id=demo_book.id,
    ) <= MIN_USED_LPN_VIRTUAL_AVAILABLE:
        added_used_lpn_quantity = USED_LPN_REPLENISH_QUANTITY

        for _ in range(added_used_lpn_quantity):
            session.add(
                InventoryUsedItem(
                    book_id=demo_book.id,
                    location_id=used_location.id,
                    lpn_barcode=f"LPN-DEMO-{uuid4().hex.upper()}",
                    ubci_score=90,
                    discount_rate=Decimal("0.00"),
                    sale_price=demo_book.base_price,
                    condition_grade=ConditionGrade.EXCELLENT,
                    status=UsedInventoryStatus.AVAILABLE,
                    certificate_url="https://example.com/certificates/demo",
                ),
            )

        demo_book.virtual_stock = (
            (demo_book.virtual_stock or 0)
            + added_used_lpn_quantity
        )
        session.add(demo_book)

    session.flush()

    return DemoInventoryEnsureResult(
        demo_book=demo_book,
        new_inventory=new_inventory,
        new_location=new_location,
        used_location=used_location,
        added_new_stock_quantity=added_new_stock_quantity,
        added_used_lpn_quantity=added_used_lpn_quantity,
    )


# 데모 전용 ISBN의 도서 마스터를 조회하고, 없으면 생성한다.
def _get_or_create_demo_book(
    session: Session,
) -> Book:
    book = session.exec(
        select(Book).where(
            Book.isbn == DEMO_OUTBOUND_BOOK_ISBN
        )
    ).first()

    if book is not None:
        return book

    book = Book(
        title=DEMO_OUTBOUND_BOOK_TITLE,
        isbn=DEMO_OUTBOUND_BOOK_ISBN,
        publisher="Demo Outbound Publisher",
        category=BookCategory.NOVEL,
        standard_size=StandardSize.A5,
        thickness_mm=20,
        base_price=18000,
        virtual_stock=0,
    )
    session.add(book)
    session.flush()

    return book


# 바코드 기준으로 데모 로케이션을 조회하고, 없으면 생성한다.
def _get_or_create_location(
    session: Session,
    barcode: str,
    zone: str,
    rack: str,
    shelf: str,
) -> Location:
    location = session.exec(
        select(Location).where(Location.barcode == barcode)
    ).first()

    if location is not None:
        return location

    location = Location(
        zone=zone,
        rack=rack,
        shelf=shelf,
        barcode=barcode,
    )
    session.add(location)
    session.flush()

    return location


# 특정 도서·로케이션의 신간 재고 행을 조회하고, 없으면 생성한다.
def _get_or_create_inventory(
    session: Session,
    book_id: UUID,
    location_id: UUID,
) -> Inventory:
    inventory = session.exec(
        select(Inventory).where(
            Inventory.book_id == book_id,
            Inventory.location_id == location_id,
        )
    ).first()

    if inventory is not None:
        return inventory

    inventory = Inventory(
        book_id=book_id,
        location_id=location_id,
        quantity=0,
        reserved_quantity=0,
    )
    session.add(inventory)
    session.flush()

    return inventory


# 신간 실재고·예약 수량·PENDING 주문을 반영한 가상 가용 수량을 계산한다.
def _get_new_stock_virtual_available_quantity(
    session: Session,
    book_id: UUID,
    inventory: Inventory,
) -> int:
    pending_quantity = session.exec(
        select(func.sum(OrderItem.quantity))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.PENDING,
            OrderItem.book_id == book_id,
            OrderItem.condition_grade.is_(None),
        )
    ).one()

    return (
        inventory.quantity
        - inventory.reserved_quantity
        - int(pending_quantity or 0)
    )


# AVAILABLE 중고 LPN 수에서 PENDING 중고 주문 수를 제외해 가상 가용 수량을 계산한다.
def _get_used_lpn_virtual_available_quantity(
    session: Session,
    book_id: UUID,
) -> int:
    available_lpn_count = session.exec(
        select(func.count(InventoryUsedItem.id)).where(
            InventoryUsedItem.book_id == book_id,
            InventoryUsedItem.condition_grade
            == ConditionGrade.EXCELLENT,
            InventoryUsedItem.status
            == UsedInventoryStatus.AVAILABLE,
        )
    ).one()

    pending_order_count = session.exec(
        select(func.count(OrderItem.id))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.PENDING,
            OrderItem.book_id == book_id,
            OrderItem.condition_grade
            == ConditionGrade.EXCELLENT,
        )
    ).one()

    return int(available_lpn_count or 0) - int(
        pending_order_count or 0
    )