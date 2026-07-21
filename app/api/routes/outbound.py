from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
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
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_id": "00000000-0000-4000-8000-000000000010"
            }
        }
    )

    order_id: UUID = Field(description="출고 처리할 PENDING 주문 ID")


class PickingListItem(BaseModel):
    book_id: UUID = Field(description="피킹할 도서 마스터 ID")
    location: str = Field(description="피킹할 로케이션 바코드")
    quantity: int = Field(description="해당 로케이션에서 피킹할 수량")


class PickResponse(BaseModel):
    order_id: UUID = Field(description="출고 처리된 주문 ID")
    status: OrderStatus = Field(description="출고 처리 후 주문 상태")
    recommended_box: str = Field(
        description="추천 박스명. 현재는 3D Bin Packing 미구현으로 임시 값 반환",
    )
    picking_list: List[PickingListItem] = Field(
        description="로케이션별 피킹 목록",
    )


@router.post(
    "/pick",
    response_model=PickResponse,
    operation_id="pickNewStockOrder",
    summary="정상 신간 주문 피킹 및 재고 차감",
    description=(
        "PENDING 주문의 정상 신간 묶음 재고를 오래된 재고부터 차감하고 "
        "주문을 SHIPPED로 변경합니다. 주문과 재고 행은 PostgreSQL Lock으로 "
        "보호되며, 한 품목이라도 부족하면 전체 트랜잭션을 롤백합니다."
    ),
    responses={
        404: {"description": "출고할 주문을 찾을 수 없음"},
        409: {
            "description": "주문 상태 오류, 주문 품목 없음 또는 재고 부족",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "message": "Insufficient stock for order item",
                            "book_id": "00000000-0000-4000-8000-000000000001",
                            "requested_quantity": 10,
                            "missing_quantity": 3,
                        }
                    }
                }
            },
        },
    },
)
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
