from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    Book,
    ConditionGrade,
    Inventory,
    InventoryLog,
    InventoryTransactionType,
    Location,
    Order,
    OrderItem,
    OrderStatus,
)

router = APIRouter()


class PickRequest(BaseModel):
    order_id: UUID


class PickingListItem(BaseModel):
    book_id: UUID
    location: str
    quantity: int


class PickResponse(BaseModel):
    order_id: UUID
    status: OrderStatus
    recommended_box: str
    picking_list: List[PickingListItem]


@router.post("/pick", response_model=PickResponse)
def pick_order(
    request: PickRequest,
    session: Session = Depends(get_session),
):
    order = session.get(Order, request.order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    if order.status != OrderStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order cannot be picked from status {order.status}",
        )

    order_items = session.exec(
        select(OrderItem).where(OrderItem.order_id == order.id)
    ).all()
    if not order_items:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Order has no order items",
        )

    allocations: list[tuple[OrderItem, Inventory, int, str]] = []
    picked_location_by_order_item: dict[UUID, UUID] = {}

    for order_item in order_items:
        remaining_quantity = order_item.quantity
        inventory_rows = session.exec(
            select(Inventory)
            .where(
                Inventory.book_id == order_item.book_id,
                Inventory.quantity > 0,
            )
            .order_by(Inventory.created_at)
        ).all()

        for inventory in inventory_rows:
            if remaining_quantity <= 0:
                break

            picked_quantity = min(inventory.quantity, remaining_quantity)
            location = session.get(Location, inventory.location_id)
            picked_location = location.barcode if location else str(inventory.location_id)
            allocations.append((order_item, inventory, picked_quantity, picked_location))
            picked_location_by_order_item.setdefault(order_item.id, inventory.location_id)
            remaining_quantity -= picked_quantity

        if remaining_quantity > 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Insufficient stock for order item",
                    "book_id": str(order_item.book_id),
                    "requested_quantity": order_item.quantity,
                    "missing_quantity": remaining_quantity,
                },
            )

    picking_list: list[PickingListItem] = []

    for order_item, inventory, picked_quantity, picked_location in allocations:
        inventory.quantity -= picked_quantity
        inventory.updated_at = datetime.utcnow()

        book = session.get(Book, inventory.book_id)
        if book is not None:
            book.virtual_stock = max(book.virtual_stock - picked_quantity, 0)
            book.updated_at = datetime.utcnow()
            session.add(book)

        order_item.location_id = picked_location_by_order_item[order_item.id]
        session.add(order_item)

        session.add(
            InventoryLog(
                transaction_type=InventoryTransactionType.OUTBOUND,
                book_id=inventory.book_id,
                condition_grade=ConditionGrade.MINT,  # 지금 이건 신간 출고로 한정.
                quantity_change=-picked_quantity,
                picked_location=picked_location,
            )
        )
        picking_list.append(
            PickingListItem(
                book_id=inventory.book_id,
                location=picked_location,
                quantity=picked_quantity,
            )
        )

    order.status = OrderStatus.SHIPPED  # 작업자 피킹 확인 상태인 PICKED를 거치게 수정할 수도 있습니다.
    session.add(order)
    session.commit()
    session.refresh(order)

    return PickResponse(
        order_id=order.id,
        status=order.status,
        recommended_box="2호",  # 이건 3D Bin Packing 구현이 아직 멀어서 임시로 mock 데이터를 두었습니다.
        picking_list=picking_list,
    )
