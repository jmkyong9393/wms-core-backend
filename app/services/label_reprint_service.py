from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.wms import (
    InboundItem,
    InventoryUsedItem,
    UsedInventoryStatus,
)
from app.schemas.label import LabelType
from app.services.zpl_label_service import (
    build_lpn_label_zpl,
    build_ubci_label_zpl,
)


def build_label_reprint_zpl(
    session: Session,
    lpn_barcode: str,
    label_type: LabelType,
) -> str:
    """
    작업자가 요청한 라벨 유형에 맞는 재출력용 ZPL을 생성한다.

    이 함수는 DB 상태를 변경하거나 프린터로 전송하지 않는다.
    입고·검수·재고 데이터가 라벨 출력 조건을 충족하는지만 검증한다.
    """
    inbound_item = session.exec(
        select(InboundItem).where(
            InboundItem.lpn_barcode == lpn_barcode,
        )
    ).first()

    if (
        inbound_item is None
        or inbound_item.certificate_token is None
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LPN label source was not found",
        )

    if label_type == LabelType.LPN:
        return build_lpn_label_zpl(
            lpn_barcode=inbound_item.lpn_barcode,
            certificate_token=inbound_item.certificate_token,
        )

    inventory_item = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.lpn_barcode == lpn_barcode,
        )
    ).first()

    if inventory_item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "UBCI label is available after sellable inventory "
                "is created"
            ),
        )

    if inventory_item.status == UsedInventoryStatus.SHIPPED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "UBCI label cannot be reprinted for a shipped item"
            ),
        )

    if inventory_item.ubci_score is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="UBCI score is not confirmed",
        )

    return build_ubci_label_zpl(
        lpn_barcode=inventory_item.lpn_barcode,
        certificate_token=inbound_item.certificate_token,
        condition_grade=inventory_item.condition_grade.value,
        ubci_score=inventory_item.ubci_score,
    )