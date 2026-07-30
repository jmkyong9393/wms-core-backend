from dataclasses import dataclass
from uuid import uuid4

from app.models.wms import Order


WAYBILL_PREFIX = "WB"
DEFAULT_SHIPPING_CARRIER = "MOCK_COURIER"


@dataclass(frozen=True)
class WaybillIssueResult:
    waybill_number: str
    shipping_carrier: str
    barcode_value: str


def issue_waybill_for_order(
    order: Order,
) -> WaybillIssueResult:
    """
    주문에 연결된 송장이 있으면 기존 송장을 반환하고,
    없으면 새 송장 번호를 발급해 주문 객체에 저장한다.

    DB commit은 호출한 출고 확정 서비스/라우터에서 처리한다.
    """
    if order.waybill_number is None:
        order.waybill_number = (
            f"{WAYBILL_PREFIX}-{uuid4().hex.upper()}"
        )
        order.shipping_carrier = DEFAULT_SHIPPING_CARRIER

    return WaybillIssueResult(
        waybill_number=order.waybill_number,
        shipping_carrier=(
            order.shipping_carrier
            or DEFAULT_SHIPPING_CARRIER
        ),
        # 현재 바코드에는 송장 번호 자체를 인코딩한다.
        barcode_value=order.waybill_number,
    )