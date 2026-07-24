from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.wms import (
    ConditionGrade,
    InventoryUsedItem,
    Location,
    OrderItem,
    UsedInventoryStatus,
)


@dataclass(frozen=True)
class FifoLpnCandidate:
    inventory_used_item: InventoryUsedItem
    picked_location: str


def select_fifo_lpn_candidates(
    session: Session,
    order_item: OrderItem,
    excluded_inventory_ids: set[UUID],
) -> list[FifoLpnCandidate]:
    condition_grade = order_item.condition_grade
    if condition_grade is None:
        raise ValueError("FIFO LPN selection requires condition grade")

    statement = select(InventoryUsedItem).where(
        InventoryUsedItem.book_id == order_item.book_id,
        InventoryUsedItem.condition_grade == condition_grade,
        InventoryUsedItem.status == UsedInventoryStatus.AVAILABLE,
    )
    if excluded_inventory_ids:
        statement = statement.where(
            InventoryUsedItem.id.notin_(excluded_inventory_ids)
        )

    inventory_items = session.exec(
        statement
        .order_by(InventoryUsedItem.stocked_at, InventoryUsedItem.id)
        .limit(order_item.quantity)
        .with_for_update(skip_locked=True)
    ).all()

    if len(inventory_items) < order_item.quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Insufficient graded LPN inventory",
                "book_id": str(order_item.book_id),
                "condition_grade": condition_grade.value,
                "requested_quantity": order_item.quantity,
                "available_quantity": len(inventory_items),
                "missing_quantity": order_item.quantity - len(inventory_items),
            },
        )

    candidates: list[FifoLpnCandidate] = []
    for inventory_item in inventory_items:
        location = session.get(Location, inventory_item.location_id)
        picked_location = (
            location.barcode if location else str(inventory_item.location_id)
        )
        candidates.append(
            FifoLpnCandidate(
                inventory_used_item=inventory_item,
                picked_location=picked_location,
            )
        )

    return candidates
