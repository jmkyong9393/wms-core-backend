from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    Board,
    BoardPost,
    Book,
    ConditionGrade,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    InspectionMode,
    Inventory,
    InventoryLog,
    InventoryTransactionType,
    InventoryUsedItem,
    Location,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    PostCategory,
    ReturnJob,
    ReturnJobStatus,
    StandardSize,
    TicketStatus,
    User,
    UserRole,
)

router = APIRouter()


def _count(session: Session, model: type) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


@router.post("/seed")
def seed_mock_data(session: Session = Depends(get_session)):
    book = Book(
        title="Mock WMS Book",
        isbn="9780000000000",
        standard_size=StandardSize.A5,
        thickness_mm=22,
        base_price=15000,
        virtual_stock=1,
    )
    session.add(book)
    session.flush()

    location = Location(
        zone="A",
        rack="1",
        shelf="3",
        barcode=f"A-1-3-{str(book.id)[:8]}",
    )
    session.add(location)
    session.flush()

    inbound_job = InboundJob(
        inbound_type=InboundType.USED_PURCHASE,
        status=InboundStatus.CHECKING,
        supplier_name="mock-supplier",
    )
    session.add(inbound_job)
    session.flush()

    inbound_item = InboundItem(
        inbound_job_id=inbound_job.id,
        book_id=book.id,
        quantity=1,
    )
    session.add(inbound_item)

    inventory = Inventory(
        book_id=book.id,
        location_id=location.id,
        quantity=1,
    )
    session.add(inventory)

    used_item = InventoryUsedItem(
        book_id=book.id,
        location_id=location.id,
        lpn_barcode=f"LPN-{str(book.id)[:8]}",
        ubci_score=95,
        condition_grade=ConditionGrade.MINT,
        certificate_url="https://example.com/certificates/mock",
    )
    session.add(used_item)

    order = Order(
        customer_name="Mock B2B Customer",
        type=OrderType.B2B_ORDER,
        total_price=15000,
        status=OrderStatus.PENDING,
    )
    session.add(order)
    session.flush()

    order_item = OrderItem(
        order_id=order.id,
        book_id=book.id,
        quantity=1,
        unit_price=book.base_price,
        final_price=book.base_price,
    )
    session.add(order_item)

    return_job = ReturnJob(
        order_id=order.id,
        book_id=book.id,
        mode = InspectionMode.RETURN,
        status=ReturnJobStatus.PROCESSING,
        image_paths=["/returns/mock/img1.jpg"],
        ubci_score=95,
        agent_logs={"vision": {"status": "mock"}},
        final_report="Mock inspection report",
    )
    session.add(return_job)
    session.flush()

    inventory_log = InventoryLog(
        transaction_type=InventoryTransactionType.RETURN_RESTOCK,
        book_id=book.id,
        condition_grade=ConditionGrade.MINT,
        quantity_change=1,
        target_lpn=used_item.lpn_barcode,
        picked_location=location.barcode,
    )
    session.add(inventory_log)

    user = User(
        employee_id="mock-worker",
        email="mock-worker@example.com",
        name="Mock Worker",
        password_hash="mock-password-hash",
        role=UserRole.WORKER,
    )
    session.add(user)
    session.flush()

    board = Board(
        job_id=return_job.id,
        ticket_status=TicketStatus.TODO,
    )
    session.add(board)

    board_post = BoardPost(
        author_id=user.id,
        category=PostCategory.MANUAL,
        title="Mock manual",
        content="Mock board post content",
        attachment_paths=["/manuals/mock.pdf"],
    )
    session.add(board_post)

    session.commit()

    return {
        "message": "Mock data seeded",
        "book_id": str(book.id),
        "inbound_job_id": str(inbound_job.id),
        "return_job_id": str(return_job.id),
        "order_id": str(order.id),
    }


@router.post("/seed/order-outbound")
def seed_order_outbound_data(session: Session = Depends(get_session)):
    book = Book(
        title="Order Outbound Seed Book",
        isbn="9781111111111",
        standard_size=StandardSize.A5,
        thickness_mm=20,
        base_price=18000,
        virtual_stock=5,
    )
    session.add(book)
    session.flush()

    location = Location(
        zone="A",
        rack="2",
        shelf="1",
        barcode=f"A-2-1-{str(book.id)[:8]}",
    )
    session.add(location)
    session.flush()

    inventory = Inventory(
        book_id=book.id,
        location_id=location.id,
        quantity=5,
    )
    session.add(inventory)
    session.commit()
    session.refresh(inventory)

    return {
        "message": "Order outbound seed data created",
        "book": {
            "id": str(book.id),
            "title": book.title,
            "base_price": book.base_price,
        },
        "location": {
            "id": str(location.id),
            "barcode": location.barcode,
        },
        "inventory": {
            "id": str(inventory.id),
            "quantity": inventory.quantity,
        },
    }


@router.get("/summary")
def get_mock_summary(session: Session = Depends(get_session)):
    return {
        "books": _count(session, Book),
        "inbound_jobs": _count(session, InboundJob),
        "inbound_items": _count(session, InboundItem),
        "locations": _count(session, Location),
        "inventory": _count(session, Inventory),
        "inventory_used_items": _count(session, InventoryUsedItem),
        "orders": _count(session, Order),
        "order_items": _count(session, OrderItem),
        "return_jobs": _count(session, ReturnJob),
        "inventory_logs": _count(session, InventoryLog),
        "users": _count(session, User),
        "boards": _count(session, Board),
        "board_posts": _count(session, BoardPost),
    }
# 여기서 원하는 column에 대한 값 확인도 가능.
