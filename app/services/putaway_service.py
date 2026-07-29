from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import text, update
from sqlmodel import Session, select

from app.domain.warehouse_location_policy import zone_for_grade
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    Inventory,
    InventoryLog,
    InventoryTransactionType,
    InventoryUsedItem,
    Location,
    PutawayJob,
    PutawayStatus,
    ReturnJob,
    ReturnJobStatus,
    UsedInventoryStatus,
)
from app.services.lpn_service import build_public_qr_url


PutawayInventoryKind = Literal[
    "NEW_AGGREGATE",
    "USED_ITEM",
    "REJECT_HOLD",
]


@dataclass(frozen=True)
class PutawayConfirmation:
    inbound_item: InboundItem
    putaway_job: PutawayJob
    location: Location
    inventory_kind: PutawayInventoryKind
    inventory_id: UUID | None
    stock_changed: bool


def confirm_putaway(
    session: Session,
    lpn_barcode: str,
) -> PutawayConfirmation:
    inbound_item = session.exec(
        select(InboundItem)
        .where(InboundItem.lpn_barcode == lpn_barcode)
        .with_for_update()
    ).first()
    if inbound_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbound LPN item not found",
        )

    putaway_job = session.exec(
        select(PutawayJob)
        .where(PutawayJob.inbound_item_id == inbound_item.id)
        .with_for_update()
    ).first()
    if putaway_job is None or inbound_item.condition_grade is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LPN is not ready for putaway",
        )
    if putaway_job.status == PutawayStatus.CLEARED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cleared reject LPN cannot be put away",
        )

    inbound_job = session.exec(
        select(InboundJob)
        .where(InboundJob.id == inbound_item.inbound_job_id)
        .with_for_update()
    ).first()
    location = session.get(Location, putaway_job.location_id)
    if inbound_job is None or location is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LPN putaway references missing lifecycle data",
        )
    if location.zone != zone_for_grade(inbound_item.condition_grade):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assigned location does not match condition grade",
        )

    if putaway_job.status == PutawayStatus.COMPLETED:
        return _build_completed_replay(
            session=session,
            inbound_item=inbound_item,
            putaway_job=putaway_job,
            location=location,
        )

    now = datetime.utcnow()
    inventory_kind, inventory_id = _apply_inventory_movement(
        session=session,
        inbound_item=inbound_item,
        inbound_job=inbound_job,
        location=location,
        now=now,
    )

    putaway_job.status = PutawayStatus.COMPLETED
    putaway_job.completed_at = now
    putaway_job.updated_at = now
    inbound_job.status = InboundStatus.COMPLETED
    inbound_job.updated_at = now
    session.add(putaway_job)
    session.add(inbound_job)
    session.flush()

    return PutawayConfirmation(
        inbound_item=inbound_item,
        putaway_job=putaway_job,
        location=location,
        inventory_kind=inventory_kind,
        inventory_id=inventory_id,
        stock_changed=inventory_id is not None,
    )


def _apply_inventory_movement(
    session: Session,
    inbound_item: InboundItem,
    inbound_job: InboundJob,
    location: Location,
    now: datetime,
) -> tuple[PutawayInventoryKind, UUID | None]:
    grade = inbound_item.condition_grade
    if grade is None:
        raise RuntimeError("Putaway item does not have condition grade")
    if grade == ConditionGrade.REJECT:
        return "REJECT_HOLD", None
    if grade == ConditionGrade.NEW:
        inventory = _increase_new_inventory(
            session=session,
            inbound_item=inbound_item,
            location=location,
            now=now,
        )
        _record_inventory_movement(
            session=session,
            inbound_item=inbound_item,
            inbound_type=inbound_job.inbound_type,
            location=location,
        )
        _increase_virtual_stock(session, inbound_item.book_id, now)
        return "NEW_AGGREGATE", inventory.id

    return_job = session.exec(
        select(ReturnJob)
        .where(ReturnJob.inbound_item_id == inbound_item.id)
        .order_by(ReturnJob.created_at.desc())
    ).first()
    if return_job is None or return_job.status != ReturnJobStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approved inspection result is required before putaway",
        )
    if inbound_item.certificate_token is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound LPN does not have a certificate token",
        )

    inventory_item = InventoryUsedItem(
        book_id=inbound_item.book_id,
        location_id=location.id,
        return_job_id=return_job.id,
        lpn_barcode=inbound_item.lpn_barcode or "",
        ubci_score=return_job.ubci_score,
        condition_grade=grade,
        status=UsedInventoryStatus.AVAILABLE,
        certificate_url=build_public_qr_url(inbound_item.certificate_token),
        stocked_at=now,
    )
    session.add(inventory_item)
    session.flush()
    _record_inventory_movement(
        session=session,
        inbound_item=inbound_item,
        inbound_type=inbound_job.inbound_type,
        location=location,
    )
    _increase_virtual_stock(session, inbound_item.book_id, now)
    return "USED_ITEM", inventory_item.id


def _increase_new_inventory(
    session: Session,
    inbound_item: InboundItem,
    location: Location,
    now: datetime,
) -> Inventory:
    lock_key = f"inventory:{inbound_item.book_id}:{location.id}"
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:lock_key, 0)"
            ")"
        ).bindparams(lock_key=lock_key)
    )
    inventory = session.exec(
        select(Inventory)
        .where(
            Inventory.book_id == inbound_item.book_id,
            Inventory.location_id == location.id,
        )
        .with_for_update()
    ).first()
    if inventory is None:
        inventory = Inventory(
            book_id=inbound_item.book_id,
            location_id=location.id,
            quantity=0,
        )
        session.add(inventory)
        session.flush()

    inventory.quantity += 1
    inventory.updated_at = now
    session.add(inventory)
    return inventory


def _record_inventory_movement(
    session: Session,
    inbound_item: InboundItem,
    inbound_type: InboundType,
    location: Location,
) -> None:
    transaction_type = (
        InventoryTransactionType.RETURN_RESTOCK
        if inbound_type == InboundType.CUSTOMER_RETURN
        else InventoryTransactionType.INBOUND
    )
    session.add(
        InventoryLog(
            transaction_type=transaction_type,
            book_id=inbound_item.book_id,
            condition_grade=inbound_item.condition_grade,
            quantity_change=1,
            target_lpn=inbound_item.lpn_barcode,
            picked_location=location.barcode,
        )
    )


def _increase_virtual_stock(
    session: Session,
    book_id: UUID,
    now: datetime,
) -> None:
    session.exec(
        update(Book)
        .where(Book.id == book_id)
        .values(
            virtual_stock=Book.virtual_stock + 1,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )


def _build_completed_replay(
    session: Session,
    inbound_item: InboundItem,
    putaway_job: PutawayJob,
    location: Location,
) -> PutawayConfirmation:
    grade = inbound_item.condition_grade
    if grade == ConditionGrade.REJECT:
        inventory_kind: PutawayInventoryKind = "REJECT_HOLD"
        inventory_id = None
    elif grade == ConditionGrade.NEW:
        inventory_kind = "NEW_AGGREGATE"
        inventory = session.exec(
            select(Inventory).where(
                Inventory.book_id == inbound_item.book_id,
                Inventory.location_id == location.id,
            )
        ).first()
        if inventory is None:
            raise RuntimeError("Completed new putaway is missing inventory")
        inventory_id = inventory.id
    else:
        inventory_kind = "USED_ITEM"
        inventory_item = session.exec(
            select(InventoryUsedItem).where(
                InventoryUsedItem.lpn_barcode == inbound_item.lpn_barcode
            )
        ).first()
        if inventory_item is None:
            raise RuntimeError("Completed used putaway is missing inventory")
        inventory_id = inventory_item.id

    return PutawayConfirmation(
        inbound_item=inbound_item,
        putaway_job=putaway_job,
        location=location,
        inventory_kind=inventory_kind,
        inventory_id=inventory_id,
        stock_changed=False,
    )
