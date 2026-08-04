import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session, select

from app.api.dependencies.auth import require_wms_operator
from app.core.database import get_session
from app.models.wms import (
    Book,
    InboundItem,
    InventoryUsedItem,
    Location,
    User,
)
from app.schemas.label import (
    LabelPrintStatus,
    LabelReprintResponse,
    LabelType,
)
from app.schemas.lpn import (
    LpnBookDetail,
    LpnDetailResponse,
    LpnLocationDetail,
)
from app.schemas.lpn_scan import LpnScanResponse
from app.services.label_printer_service import (
    send_zpl_to_label_printer,
)
from app.services.label_reprint_service import (
    build_label_reprint_zpl,
)
from app.services.lpn_scan_service import get_lpn_scan_detail
from app.services.lpn_service import build_public_qr_url


router = APIRouter()

logger = logging.getLogger(__name__)

@router.get(
    "/scan/{certificate_token}",
    response_model=LpnScanResponse,
    operation_id="getLpnScanDetail",
    summary="작업자용 LPN QR 스캔 상세 조회",
    description=(
        "작업자 모바일 앱이 LPN 라벨 QR의 스캔 토큰으로 "
        "입고·검수·재촬영·재고 편입 상태와 현재 로케이션을 조회합니다."
    ),
    responses={
        401: {"description": "인증 토큰이 없거나 유효하지 않음"},
        403: {"description": "WMS 작업자 권한이 없음"},
        404: {"description": "LPN 스캔 토큰을 찾을 수 없음"},
    },
)
def get_lpn_scan(
    certificate_token: str = Path(
        min_length=32,
        max_length=128,
        description="LPN QR에 포함된 공개 스캔 토큰",
    ),
    session: Session = Depends(get_session),
    _: User = Depends(require_wms_operator),
) -> LpnScanResponse:
    """
    작업자 앱이 동일 LPN QR을 반복 스캔해도 최신 업무 상태를 반환한다.
    """
    return get_lpn_scan_detail(
        session=session,
        certificate_token=certificate_token,
    )

@router.post(
    "/{lpn_barcode}/labels/{label_type}/reprint",
    response_model=LabelReprintResponse,
    operation_id="reprintLpnLabel",
    summary="작업자용 LPN·UBCI 라벨 재출력",
    description=(
        "작업자가 프린터 오류, 용지 걸림, 라벨 훼손 등의 사유로 "
        "LPN 또는 UBCI 라벨을 재출력합니다. "
        "재출력은 프린터 전송만 수행하며 입고·검수·재고 데이터를 변경하지 않습니다. "
        "UBCI 라벨은 검수 승인 후 판매 가능 단품 재고가 존재하는 경우에만 출력할 수 있습니다."
    ),
    responses={
        401: {"description": "인증 토큰이 없거나 유효하지 않음"},
        403: {"description": "WMS 작업자 권한이 없음"},
        404: {"description": "LPN 라벨 원본 데이터를 찾을 수 없음"},
        409: {
            "description": (
                "UBCI 라벨 출력 조건 미충족 또는 출고 완료 단품"
            )
        },
    },
)
def reprint_lpn_label(
    lpn_barcode: str = Path(
        min_length=1,
        description="재출력할 물리 단품 LPN",
        examples=["LPN-12345678123456781234567812345678"],
    ),
    label_type: LabelType = Path(
        description="재출력할 라벨 유형(LPN 또는 UBCI)",
    ),
    session: Session = Depends(get_session),
    _: User = Depends(require_wms_operator),
) -> LabelReprintResponse:
    """
    재출력은 이미 저장된 LPN·검수·재고 데이터를 조회하여 ZPL만 다시 전송한다.

    프린터 오류는 DB 데이터를 변경하거나 기존 입고·검수 상태를 롤백하지 않는다.
    """
    zpl = build_label_reprint_zpl(
        session=session,
        lpn_barcode=lpn_barcode,
        label_type=label_type,
    )

    try:
        print_result = send_zpl_to_label_printer(zpl)
    except Exception:
        logger.exception(
            "Failed to reprint label. lpn_barcode=%s label_type=%s",
            lpn_barcode,
            label_type.value,
        )
        return LabelReprintResponse(
            lpn_barcode=lpn_barcode,
            label_type=label_type,
            label_print_status=LabelPrintStatus.FAILED,
            label_print_error=(
                "라벨 재출력에 실패했습니다. "
                "프린터 상태를 확인한 뒤 다시 시도해주세요."
            ),
        )

    return LabelReprintResponse(
        lpn_barcode=lpn_barcode,
        label_type=label_type,
        label_print_status=(
            LabelPrintStatus.SKIPPED
            if print_result.skipped
            else LabelPrintStatus.SENT
        ),
        label_print_error=None,
    )

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

    inbound_item = session.exec(
        select(InboundItem).where(
            InboundItem.lpn_barcode == inventory_item.lpn_barcode,
        )
    ).first()
    if inbound_item is None or inbound_item.certificate_token is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LPN inventory does not have a certificate token",
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
        base_price=book.base_price,
        discount_rate=inventory_item.discount_rate,
        sale_price=inventory_item.sale_price,
        pricing_status=(
            "AGENT_PRICED"
            if inventory_item.sale_price is not None
            else "PENDING"
        ),
        location=LpnLocationDetail(
            id=location.id,
            barcode=location.barcode,
            zone=location.zone,
            rack=location.rack,
            shelf=location.shelf,
        ),
        stocked_at=inventory_item.stocked_at,
        certificate_url=build_public_qr_url(inbound_item.certificate_token),
    )
