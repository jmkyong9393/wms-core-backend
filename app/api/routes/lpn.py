from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session, select

from app.api.dependencies.auth import require_wms_operator
from app.core.database import get_session
from app.models.wms import Book, InventoryUsedItem, Location, User
from app.schemas.lpn import (
    LpnBookDetail,
    LpnDetailResponse,
    LpnLocationDetail,
)
from app.services.lpn_service import build_public_qr_url


router = APIRouter()


@router.get(
    "/{lpn_barcode}",
    response_model=LpnDetailResponse,
    operation_id="getLpnDetail",
    summary="LPN 단품 재고 상세 조회",
    description=(
        "작업자가 스캔한 LPN을 기준으로 중고·반품 단품 재고의 도서 정보, "
        "품질 등급, UBCI 점수, 현재 상태와 보관 로케이션을 조회합니다. "
        "창고 내부 정보이므로 WORKER, ADMIN, MASTER 권한이 필요합니다. "
        "고객 공개용 품질 정보는 Certificate API를 사용합니다."
    ),
    responses={
        401: {"description": "인증 토큰이 없거나 유효하지 않음"},
        403: {"description": "WMS 작업자 권한이 없음"},
        404: {"description": "등록된 LPN 단품 재고를 찾을 수 없음"},
        500: {"description": "LPN에 연결된 도서 또는 로케이션 데이터가 유실됨"},
    },
)
def get_lpn_detail(
    lpn_barcode: str = Path(
        min_length=1,
        description="조회할 물리 도서의 LPN 바코드",
        examples=["LPN-12345678123456781234567812345678"],
    ),
    session: Session = Depends(get_session),
    _: User = Depends(require_wms_operator),
) -> LpnDetailResponse:
    inventory_item = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.lpn_barcode == lpn_barcode,
        )
    ).first()
    if inventory_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LPN inventory item not found",
        )

    book = session.get(Book, inventory_item.book_id)
    location = session.get(Location, inventory_item.location_id)
    if book is None or location is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LPN inventory references missing master data",
        )

    return LpnDetailResponse(
        lpn_barcode=inventory_item.lpn_barcode,
        book=LpnBookDetail(
            id=book.id,
            isbn=book.isbn,
            title=book.title,
            publisher=book.publisher,
        ),
        inventory_status=inventory_item.status,
        condition_grade=inventory_item.condition_grade,
        ubci_score=inventory_item.ubci_score,
        location=LpnLocationDetail(
            id=location.id,
            barcode=location.barcode,
            zone=location.zone,
            rack=location.rack,
            shelf=location.shelf,
        ),
        stocked_at=inventory_item.stocked_at,
        certificate_url=build_public_qr_url(inventory_item.lpn_barcode),
    )
