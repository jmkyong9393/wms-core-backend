from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.wms import (
    InboundItem,
    InventoryUsedItem,
    ReturnJob,
    UsedInventoryStatus,
)
from app.schemas.label import LabelType
from app.domains.books.certificate_service import extract_report_summary
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

    재출력은 DB 상태를 변경하거나 프린터로 전송하지 않는다.
    출고 완료 도서와 공개 품질보증서가 아직 준비되지 않은 UBCI 라벨은
    재출력하지 않는다.
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

    # 검수 전에는 재고가 없을 수 있으므로 LPN 재출력 자체는 허용한다.
    # 단, 출고 완료 재고가 있으면 실제 창고에 없는 도서이므로 차단한다.
    inventory_item = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.lpn_barcode == lpn_barcode,
        )
    ).first()

    if (
        inventory_item is not None
        and inventory_item.status == UsedInventoryStatus.SHIPPED
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Label cannot be reprinted for a shipped item",
        )

    if label_type == LabelType.LPN:
        return build_lpn_label_zpl(
            lpn_barcode=inbound_item.lpn_barcode,
            certificate_token=inbound_item.certificate_token,
        )

    if inventory_item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "UBCI label is available after sellable inventory "
                "is created"
            ),
        )

    if inventory_item.ubci_score is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="UBCI score is not confirmed",
        )

    if inventory_item.return_job_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="UBCI label does not have inspection source data",
        )

    return_job = session.exec(
        select(ReturnJob).where(
            ReturnJob.id == inventory_item.return_job_id,
        )
    ).first()

    if (
        return_job is None
        or extract_report_summary(return_job.final_report) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Public quality certificate is not ready",
        )

    return build_ubci_label_zpl(
        lpn_barcode=inventory_item.lpn_barcode,
        certificate_token=inbound_item.certificate_token,
        condition_grade=inventory_item.condition_grade.value,
        ubci_score=inventory_item.ubci_score,
    )