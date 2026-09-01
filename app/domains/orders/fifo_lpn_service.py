from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.wms import (
    InventoryUsedItem,
    Location,
    OrderItem,
    UsedInventoryStatus,
)


@dataclass(frozen=True)
class FifoLpnCandidate:
    inventory_used_item: InventoryUsedItem
    picked_location: str


def select_fifo_lpn_candidate(
    session: Session,
    order_item: OrderItem,
    excluded_inventory_ids: set[UUID],
) -> FifoLpnCandidate:
    condition_grade = order_item.condition_grade
    if condition_grade is None:
        raise ValueError("FIFO LPN selection requires condition grade")
    if order_item.quantity != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Used order item must represent exactly one LPN",
                "order_item_id": str(order_item.id),
                "quantity": order_item.quantity,
            },
        )

    statement = select(InventoryUsedItem).where(
        InventoryUsedItem.book_id == order_item.book_id,
        InventoryUsedItem.condition_grade == condition_grade,
        InventoryUsedItem.status == UsedInventoryStatus.AVAILABLE,
        InventoryUsedItem.discount_rate.is_not(None),
        InventoryUsedItem.sale_price.is_not(None),
    )
    if excluded_inventory_ids:
        statement = statement.where(InventoryUsedItem.id.notin_(excluded_inventory_ids))

    inventory_item = session.exec(
        statement.order_by(InventoryUsedItem.stocked_at, InventoryUsedItem.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()

    if inventory_item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Insufficient graded LPN inventory",
                "book_id": str(order_item.book_id),
                "condition_grade": condition_grade.value,
                "requested_quantity": 1,
                "available_quantity": 0,
                "missing_quantity": 1,
            },
        )

    location = session.get(Location, inventory_item.location_id)
    picked_location = location.barcode if location else str(inventory_item.location_id)
    return FifoLpnCandidate(
        inventory_used_item=inventory_item,
        picked_location=picked_location,
    )
