from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    Board,
    BoardPost,
    Book,
    ConditionGrade,
    FdsReport,
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
    WeeklyInsight,
    UsedInventoryStatus,
)

from app.api.dependencies.auth import require_master

from app.services.demo_inventory_service import (
    ensure_demo_outbound_inventory,
)

router = APIRouter()


def _count(session: Session, model: type) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


@router.post("/seed")
def seed_mock_data(
    current_master: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    seed_suffix = str(uuid4().int)[-10:]
    customer_id = uuid4()
    
    book = Book(
        title="Mock WMS Book",
        isbn=f"978{seed_suffix}",
        publisher="Mock Publisher",
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
        customer_id=customer_id,
        customer_name="Mock B2B Customer",
        type=OrderType.B2B_ORDER,
        total_price=15000,
        status=OrderStatus.PENDING,
        logistics_center="SEOUL_DC",
    )
    session.add(order)
    session.flush()

    order_item = OrderItem(
        order_id=order.id,
        book_id=book.id,
        location_id=location.id,
        quantity=1,
        unit_price=book.base_price,
        final_price=book.base_price,
    )
    session.add(order_item)

    return_job = ReturnJob(
        tenant_id=current_master.tenant_id,
        order_id=order.id,
        book_id=book.id,
        mode=InspectionMode.RETURN,
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

    fds_report = FdsReport(
        customer_id=customer_id,
        fraud_score=95,
        fraud_reason="Mock repeated return pattern",
    )
    session.add(fds_report)

    weekly_insight = WeeklyInsight(
        report_week=f"2026-W28-MOCK-{seed_suffix}",
        saved_labor_cost_krw=1200000,
        top_defective_publishers={"Mock Publisher": 3},
        location_hotspots={"A-1-3": 2},
        logistics_hotspots={"SEOUL_DC": 1},
        predicted_returns=12,
    )
    session.add(weekly_insight)

    user = User(
        tenant_id=current_master.tenant_id,
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
def seed_order_outbound_data(
    _master: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    seed_suffix = str(uuid4().int)[-10:]
    book = Book(
        title="Order Outbound Seed Book",
        isbn=f"979{seed_suffix}",
        publisher="Order Seed Publisher",
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

@router.post("/seed/outbound-demo")
def seed_outbound_demo_data(
    _master: User = Depends(require_master),
    session: Session = Depends(get_session),
):
    """
    데모 출고 주문에 사용할 동일 도서의 신간·중고 재고를 준비한다.

    신간은 묶음 Inventory로,
    중고는 EXCELLENT 등급의 AVAILABLE LPN으로 생성된다.
    """
    result = ensure_demo_outbound_inventory(session)

    session.commit()
    session.refresh(result.new_inventory)

    return {
        "message": (
            "Mock outbound demo inventory ensured. "
            "One book has both new stock and used LPN inventory."
        ),
        "book": {
            "id": str(result.demo_book.id),
            "title": result.demo_book.title,
            "isbn": result.demo_book.isbn,
        },
        "new_stock": {
            "inventory_id": str(result.new_inventory.id),
            "quantity": result.new_inventory.quantity,
            "added_quantity": result.added_new_stock_quantity,
            "location_barcode": result.new_location.barcode,
        },
        "used_lpn": {
            "added_lpn_quantity": result.added_used_lpn_quantity,
            "condition_grade": ConditionGrade.EXCELLENT.value,
            "location_barcode": result.used_location.barcode,
        },
    }

@router.get("/summary")
def get_mock_summary(
    _master: User = Depends(require_master),
    session: Session = Depends(get_session),
):
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
        "fds_reports": _count(session, FdsReport),
        "weekly_insights": _count(session, WeeklyInsight),
        "users": _count(session, User),
        "boards": _count(session, Board),
        "board_posts": _count(session, BoardPost),
    }
# 여기서 원하는 column에 대한 값 확인도 가능.
