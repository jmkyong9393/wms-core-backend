from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import case, update
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
    try:
        order = session.exec(
            select(Order)
            .where(Order.id == request.order_id)
            .with_for_update()
        ).first()
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
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.book_id, OrderItem.id)
        ).all()
        if not order_items:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Order has no order items",
            )

        allocations: list[tuple[OrderItem, Inventory, int, str]] = []
        picked_location_by_order_item: dict[UUID, UUID] = {}
        picked_quantity_by_book: dict[UUID, int] = {}
        reserved_quantity_by_inventory: dict[UUID, int] = {}

        for order_item in order_items:
            remaining_quantity = order_item.quantity
            inventory_rows = session.exec(
                select(Inventory)
                .where(
                    Inventory.book_id == order_item.book_id,
                    Inventory.quantity > 0,
                )
                .order_by(Inventory.created_at, Inventory.id)
                .with_for_update()
            ).all()

            for inventory in inventory_rows:
                if remaining_quantity <= 0:
                    break

                available_quantity = (
                    inventory.quantity
                    - reserved_quantity_by_inventory.get(inventory.id, 0)
                )
                if available_quantity <= 0:
                    continue

                picked_quantity = min(available_quantity, remaining_quantity)
                location = session.get(Location, inventory.location_id)
                picked_location = (
                    location.barcode if location else str(inventory.location_id)
                )
                allocations.append(
                    (order_item, inventory, picked_quantity, picked_location)
                )
                picked_location_by_order_item.setdefault(
                    order_item.id,
                    inventory.location_id,
                )
                picked_quantity_by_book[order_item.book_id] = (
                    picked_quantity_by_book.get(order_item.book_id, 0)
                    + picked_quantity
                )
                reserved_quantity_by_inventory[inventory.id] = (
                    reserved_quantity_by_inventory.get(inventory.id, 0)
                    + picked_quantity
                )
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

            order_item.location_id = picked_location_by_order_item[order_item.id]
            session.add(order_item)

            session.add(
                InventoryLog(
                    transaction_type=InventoryTransactionType.OUTBOUND,
                    book_id=inventory.book_id,
                    condition_grade=ConditionGrade.MINT,  # 신간 출고로 한정.
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

        now = datetime.utcnow()
        for book_id in sorted(picked_quantity_by_book, key=str):
            picked_quantity = picked_quantity_by_book[book_id]
            session.exec(
                update(Book)
                .where(Book.id == book_id)
                .values(
                    virtual_stock=case(
                        (
                            Book.virtual_stock >= picked_quantity,
                            Book.virtual_stock - picked_quantity,
                        ),
                        else_=0,
                    ),
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )

        order.status = OrderStatus.SHIPPED
        order.updated_at = now
        session.add(order)
        session.commit()
        session.refresh(order)
    except Exception:
        session.rollback()
        raise

    return PickResponse(
        order_id=order.id,
        status=order.status,
        recommended_box="2호",  # 이건 3D Bin Packing 구현이 아직 멀어서 임시로 mock 데이터를 두었습니다.
        picking_list=picking_list,
    )
