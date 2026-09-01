from datetime import datetime

from sqlmodel import Session, select

from app.models.wms import (
    Order,
    OrderItem,
    OrderItemInventoryAllocation,
    OrderItemLpnAllocation,
    OrderStatus,
    OrderType,
)
from app.domains.admin.schemas.admin_dashboard import (
    OutboundDashboardOrderResponse,
    OutboundDashboardSummaryResponse,
)


RECENT_OUTBOUND_ORDER_LIMIT = 10


def get_outbound_dashboard_summary(
    session: Session,
) -> OutboundDashboardSummaryResponse:
    now = datetime.utcnow()
    today_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    active_picking_orders = session.exec(
        select(Order).where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.PICKING,
        )
    ).all()

    active_order_ids = [order.id for order in active_picking_orders]

    expected_quantity = 0
    picked_quantity = 0

    if active_order_ids:
        active_order_items = session.exec(
            select(OrderItem).where(
                OrderItem.order_id.in_(active_order_ids)
            )
        ).all()
        active_order_item_ids = [
            order_item.id
            for order_item in active_order_items
        ]

        if active_order_item_ids:
            new_allocations = session.exec(
                select(OrderItemInventoryAllocation).where(
                    OrderItemInventoryAllocation.order_item_id.in_(
                        active_order_item_ids
                    )
                )
            ).all()

            expected_quantity += sum(
                allocation.quantity
                for allocation in new_allocations
            )
            picked_quantity += sum(
                allocation.picked_quantity
                for allocation in new_allocations
            )

            used_allocations = session.exec(
                select(OrderItemLpnAllocation).where(
                    OrderItemLpnAllocation.order_item_id.in_(
                        active_order_item_ids
                    )
                )
            ).all()

            expected_quantity += len(used_allocations)
            picked_quantity += sum(
                allocation.picked_at is not None
                for allocation in used_allocations
            )

    picking_completion_rate = (
        round((picked_quantity / expected_quantity) * 100, 1)
        if expected_quantity
        else 0.0
    )

    today_shipping_label_issued_count = session.exec(
        select(Order).where(
            Order.type == OrderType.B2B_ORDER,
            Order.status == OrderStatus.SHIPPED,
            Order.shipped_at >= today_start,
            Order.waybill_number.is_not(None),
        )
    ).all()

    recent_orders = session.exec(
        select(Order)
        .where(Order.type == OrderType.B2B_ORDER)
        .order_by(
            Order.created_at.desc(),
            Order.id.desc(),
        )
        .limit(RECENT_OUTBOUND_ORDER_LIMIT)
    ).all()

    return OutboundDashboardSummaryResponse(
        active_picking_order_count=len(active_picking_orders),
        picking_completion_rate=picking_completion_rate,
        today_shipping_label_issued_count=(
            len(today_shipping_label_issued_count)
        ),
        recent_orders=[
            OutboundDashboardOrderResponse(
                id=order.id,
                customer_name=order.customer_name,
                order_type=order.type,
                total_price=order.total_price,
                status=order.status,
                waybill_number=order.waybill_number,
                created_at=order.created_at,
                shipped_at=order.shipped_at,
            )
            for order in recent_orders
        ],
    )