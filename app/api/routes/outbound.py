from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    ConditionGrade,
    Inventory,
    InventoryUsedItem,
    Location,
    Order,
    OrderItem,
    OrderItemInventoryAllocation,
    OrderItemLpnAllocation,
    OrderStatus,
    UsedInventoryStatus,
)
from app.services.fifo_lpn_service import (
    FifoLpnCandidate,
    select_fifo_lpn_candidate,
)


router = APIRouter()


class PickRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "order_id": "00000000-0000-4000-8000-000000000010",
            }
        }
    )

    order_id: UUID = Field(
        description="피킹 지시서를 생성할 PENDING 주문 ID",
    )


class PickingInstructionItem(BaseModel):
    order_item_id: UUID
    book_id: UUID
    quantity: int
    condition_grade: ConditionGrade | None = None
    lpn_barcode: str | None = None


class PickingShelfGroup(BaseModel):
    shelf: str
    items: list[PickingInstructionItem]


class PickingRackGroup(BaseModel):
    rack: str
    shelves: list[PickingShelfGroup]


class PickingZoneGroup(BaseModel):
    zone: str
    racks: list[PickingRackGroup]


class PickResponse(BaseModel):
    order_id: UUID
    status: OrderStatus
    recommended_box: str
    picking_groups: list[PickingZoneGroup]


def build_picking_groups(
    rows: list[tuple[Location, PickingInstructionItem]],
) -> list[PickingZoneGroup]:
    grouped: dict[
        str,
        dict[
            str,
            dict[str, list[PickingInstructionItem]],
        ],
    ] = {}

    for location, item in rows:
        zone = str(location.zone)
        rack = str(location.rack)
        shelf = str(location.shelf)

        grouped.setdefault(zone, {})
        grouped[zone].setdefault(rack, {})
        grouped[zone][rack].setdefault(shelf, [])
        grouped[zone][rack][shelf].append(item)

    return [
        PickingZoneGroup(
            zone=zone,
            racks=[
                PickingRackGroup(
                    rack=rack,
                    shelves=[
                        PickingShelfGroup(
                            shelf=shelf,
                            items=items,
                        )
                        for shelf, items in sorted(
                            shelves.items(),
                            key=lambda row: row[0],
                        )
                    ],
                )
                for rack, shelves in sorted(
                    racks.items(),
                    key=lambda row: row[0],
                )
            ],
        )
        for zone, racks in sorted(
            grouped.items(),
            key=lambda row: row[0],
        )
    ]


@router.post(
    "/picking-instructions",
    response_model=PickResponse,
    operation_id="createPickingInstruction",
    summary="출고 피킹 지시서 생성 및 재고 예약",
    description=(
        "PENDING 주문의 재고를 실제 차감하지 않고 예약합니다. "
        "신간은 reserved_quantity를 증가시키고, 중고 단품은 RESERVED로 "
        "변경합니다. 피킹 지시서는 Zone, Rack, Shelf 순서로 그룹화됩니다. "
        "실제 재고 차감과 SHIPPED 처리는 출고 확정 단계에서 수행합니다."
    ),
    responses={
        404: {"description": "출고 주문 또는 재고 로케이션을 찾을 수 없음"},
        409: {
            "description": "주문 상태 오류, 주문 품목 없음 또는 가용 재고 부족",
        },
    },
)
def create_picking_instruction(
    request: PickRequest,
    session: Session = Depends(get_session),
) -> PickResponse:
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
                detail=(
                    "Picking instruction can only be created "
                    f"from PENDING status. current_status={order.status}"
                ),
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

        picking_rows: list[
            tuple[Location, PickingInstructionItem]
        ] = []
        selected_used_inventory_ids: set[UUID] = set()

        for order_item in order_items:
            # 중고 단품 주문: FIFO로 LPN 한 건을 선택해 예약한다.
            if order_item.condition_grade is not None:
                candidate: FifoLpnCandidate = select_fifo_lpn_candidate(
                    session=session,
                    order_item=order_item,
                    excluded_inventory_ids=selected_used_inventory_ids,
                )
                inventory_used_item: InventoryUsedItem = (
                    candidate.inventory_used_item
                )
                location = session.get(
                    Location,
                    inventory_used_item.location_id,
                )
                if location is None:
                    raise RuntimeError(
                        "Location for used inventory item was not found"
                    )

                selected_used_inventory_ids.add(inventory_used_item.id)

                inventory_used_item.status = UsedInventoryStatus.RESERVED
                inventory_used_item.updated_at = datetime.utcnow()
                session.add(inventory_used_item)

                session.add(
                    OrderItemLpnAllocation(
                        order_item_id=order_item.id,
                        inventory_used_item_id=inventory_used_item.id,
                    )
                )

                picking_rows.append(
                    (
                        location,
                        PickingInstructionItem(
                            order_item_id=order_item.id,
                            book_id=inventory_used_item.book_id,
                            quantity=1,
                            condition_grade=(
                                inventory_used_item.condition_grade
                            ),
                            lpn_barcode=inventory_used_item.lpn_barcode,
                        ),
                    )
                )
                continue

            # 신간 주문: 실제 quantity는 차감하지 않고 reserved_quantity만 증가한다.
            remaining_quantity = order_item.quantity
            inventory_rows = session.exec(
                select(Inventory)
                .where(
                    Inventory.book_id == order_item.book_id,
                    Inventory.quantity > Inventory.reserved_quantity,
                )
                .order_by(Inventory.created_at, Inventory.id)
                .with_for_update()
            ).all()

            for inventory in inventory_rows:
                if remaining_quantity <= 0:
                    break

                available_quantity = (
                    inventory.quantity - inventory.reserved_quantity
                )
                if available_quantity <= 0:
                    continue

                reserved_quantity = min(
                    available_quantity,
                    remaining_quantity,
                )
                location = session.get(
                    Location,
                    inventory.location_id,
                )
                if location is None:
                    raise RuntimeError(
                        "Location for inventory was not found"
                    )

                inventory.reserved_quantity += reserved_quantity
                inventory.updated_at = datetime.utcnow()
                session.add(inventory)

                session.add(
                    OrderItemInventoryAllocation(
                        order_item_id=order_item.id,
                        inventory_id=inventory.id,
                        quantity=reserved_quantity,
                    )
                )

                picking_rows.append(
                    (
                        location,
                        PickingInstructionItem(
                            order_item_id=order_item.id,
                            book_id=inventory.book_id,
                            quantity=reserved_quantity,
                            condition_grade=ConditionGrade.MINT,
                        ),
                    )
                )
                remaining_quantity -= reserved_quantity

            if remaining_quantity > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Insufficient available stock for order item",
                        "book_id": str(order_item.book_id),
                        "requested_quantity": order_item.quantity,
                        "missing_quantity": remaining_quantity,
                    },
                )

        order.status = OrderStatus.PICKING
        order.updated_at = datetime.utcnow()
        session.add(order)

        session.commit()
        session.refresh(order)
    except Exception:
        session.rollback()
        raise

    return PickResponse(
        order_id=order.id,
        status=order.status,
        recommended_box="MEDIUM",
        picking_groups=build_picking_groups(picking_rows),
    )