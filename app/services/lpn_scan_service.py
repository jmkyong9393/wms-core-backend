from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.wms import (
    Book,
    InboundItem,
    InboundJob,
    InventoryUsedItem,
    Location,
    RejectedItem,
    ReturnJob,
    ReturnJobStatus,
)
from app.schemas.lpn import (
    LpnBookDetail,
    LpnLocationDetail,
)
from app.schemas.lpn_scan import LpnScanResponse


def get_lpn_scan_detail(
    session: Session,
    certificate_token: str,
) -> LpnScanResponse:
    """
    작업자 앱의 LPN QR 스캔 상세 정보를 조회한다.

    입고 직후, AI 검수 중, 재촬영 요청, 판매 가능 재고 편입,
    검수 반려 상태를 모두 동일한 certificate_token으로 조회한다.
    """
    inbound_item = session.exec(
        select(InboundItem).where(
            InboundItem.certificate_token
            == certificate_token
        )
    ).first()
    if inbound_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LPN scan token was not found",
        )

    if inbound_item.lpn_barcode is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound item does not have an LPN barcode",
        )

    inbound_job = session.get(
        InboundJob,
        inbound_item.inbound_job_id,
    )
    if inbound_job is None:
        raise RuntimeError(
            "Inbound item references a missing inbound job"
        )

    book = session.get(Book, inbound_item.book_id)
    if book is None:
        raise RuntimeError(
            "Inbound item references a missing book"
        )

    # 동일 입고 품목으로 생성된 가장 최근 검수 작업을 조회한다.
    return_job = session.exec(
        select(ReturnJob)
        .where(ReturnJob.inbound_item_id == inbound_item.id)
        .order_by(ReturnJob.created_at.desc())
    ).first()

    used_inventory_item = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.lpn_barcode
            == inbound_item.lpn_barcode
        )
    ).first()

    rejected_item = session.exec(
        select(RejectedItem).where(
            RejectedItem.inbound_item_id == inbound_item.id
        )
    ).first()

    # 판매 가능 단품 또는 반려 단품에 등록된 로케이션을 표시한다.
    stored_item = used_inventory_item or rejected_item
    location_response = None

    if stored_item is not None:
        location = session.get(
            Location,
            stored_item.location_id,
        )
        if location is None:
            raise RuntimeError(
                "Stored item references a missing location"
            )

        location_response = LpnLocationDetail(
            id=location.id,
            barcode=location.barcode,
            zone=location.zone,
            rack=location.rack,
            shelf=location.shelf,
        )

    inspection_status = (
        return_job.status
        if return_job is not None
        else None
    )
    final_grade = (
        return_job.condition_grade
        if return_job is not None
        and return_job.condition_grade is not None
        else inbound_item.condition_grade
    )
    ubci_score = (
        return_job.ubci_score
        if return_job is not None
        else None
    )

    return LpnScanResponse(
        lpn_barcode=inbound_item.lpn_barcode,
        book=LpnBookDetail(
            id=book.id,
            isbn=book.isbn,
            title=book.title,
            publisher=book.publisher,
        ),
        inbound_type=inbound_job.inbound_type,
        inbound_status=inbound_job.status,
        inspection_status=inspection_status,
        final_grade=final_grade,
        ubci_score=ubci_score,
        inventory_status=(
            used_inventory_item.status
            if used_inventory_item is not None
            else None
        ),
        rejected_item_status=(
            rejected_item.status
            if rejected_item is not None
            else None
        ),
        location=location_response,
        requires_retake=(
            inspection_status
            == ReturnJobStatus.RECHECK_REQUIRED
        ),
    )