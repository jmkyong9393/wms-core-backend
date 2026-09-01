from datetime import datetime

from sqlalchemy import update
from sqlmodel import Session, select

from app.domain.inventory_pricing_policy import (
    calculate_new_stock_default_price,
)
from app.domains.lpn.lpn_service import build_public_qr_url
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    Inventory,
    InventoryLog,
    InventoryTransactionType,
    InventoryUsedItem,
    Location,
    RejectedItem,
    ReturnJob,
    UsedInventoryStatus,
)


def admit_new_stock(
    session: Session,
    inbound_item: InboundItem,
    book: Book,
    location: Location,
) -> Inventory:
    inventory = session.exec(
        select(Inventory)
        .where(
            Inventory.book_id == book.id,
            Inventory.location_id == location.id,
        )
        .with_for_update()
    ).first()
    now = datetime.utcnow()
    default_discount_rate, default_sale_price = calculate_new_stock_default_price(book.base_price)
    if inventory is None:
        inventory = Inventory(
            book_id=book.id,
            location_id=location.id,
            quantity=inbound_item.quantity,
            discount_rate=default_discount_rate,
            sale_price=default_sale_price,
        )
    else:
        inventory.quantity += inbound_item.quantity
        # 기존 재고의 가격 정책은 유지하고, 과거 무가격 행만 기본값으로 보완한다.
        if inventory.discount_rate is None and inventory.sale_price is None:
            inventory.discount_rate = default_discount_rate
            inventory.sale_price = default_sale_price
        inventory.updated_at = now
    session.add(inventory)
    _record_inbound_log(
        session=session,
        inbound_item=inbound_item,
        location=location,
        grade=ConditionGrade.MINT,
    )
    _increase_virtual_stock(session, book.id, inbound_item.quantity, now)
    session.flush()
    return inventory


def admit_used_stock(
    session: Session,
    inbound_item: InboundItem,
    return_job: ReturnJob,
    location: Location,
    grade: ConditionGrade | None,
) -> InventoryUsedItem:
    if inbound_item.lpn_barcode is None:
        raise RuntimeError("Inbound item does not have an LPN barcode")
    certificate_url = (
        build_public_qr_url(inbound_item.certificate_token) if inbound_item.certificate_token is not None else None
    )
    now = datetime.utcnow()
    inventory_item = InventoryUsedItem(
        book_id=inbound_item.book_id,
        location_id=location.id,
        return_job_id=return_job.id,
        lpn_barcode=inbound_item.lpn_barcode,
        ubci_score=return_job.ubci_score,
        condition_grade=grade,
        status=UsedInventoryStatus.AVAILABLE,
        certificate_url=certificate_url,
        stocked_at=now,
    )
    session.add(inventory_item)
    _record_inbound_log(
        session=session,
        inbound_item=inbound_item,
        location=location,
        grade=grade,
    )
    _increase_virtual_stock(session, inbound_item.book_id, 1, now)
    session.flush()
    return inventory_item


def admit_rejected_item(
    session: Session,
    inbound_item: InboundItem,
    return_job: ReturnJob,
    location: Location,
    rejection_reason: dict | None,
) -> RejectedItem:
    if inbound_item.lpn_barcode is None:
        raise RuntimeError("Inbound item does not have an LPN barcode")
    rejected_item = RejectedItem(
        inbound_item_id=inbound_item.id,
        return_job_id=return_job.id,
        book_id=inbound_item.book_id,
        location_id=location.id,
        lpn_barcode=inbound_item.lpn_barcode,
        ubci_score=return_job.ubci_score,
        rejection_reason=rejection_reason,
    )
    session.add(rejected_item)
    session.flush()
    return rejected_item


def _record_inbound_log(
    session: Session,
    inbound_item: InboundItem,
    location: Location,
    grade: ConditionGrade,
) -> None:
    session.add(
        InventoryLog(
            transaction_type=InventoryTransactionType.INBOUND,
            book_id=inbound_item.book_id,
            condition_grade=grade,
            quantity_change=inbound_item.quantity,
            target_lpn=inbound_item.lpn_barcode,
            picked_location=location.barcode,
        )
    )


def _increase_virtual_stock(
    session: Session,
    book_id,
    quantity: int,
    now: datetime,
) -> None:
    session.exec(
        update(Book)
        .where(Book.id == book_id)
        .values(
            virtual_stock=Book.virtual_stock + quantity,
            updated_at=now,
        )
    )
