from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session, select

from app.api.dependencies.auth import require_wms_operator
from app.core.database import get_session
from app.models.wms import (
    Book,
    InboundItem,
    InboundJob,
    InventoryUsedItem,
    Location,
    PutawayJob,
    User,
)
from app.schemas.lpn import (
    LpnBookDetail,
    LpnDetailResponse,
    LpnLocationDetail,
    LpnPutawayBookDetail,
    LpnPutawayResponse,
)
from app.schemas.putaway import PutawayConfirmationResponse
from app.services.lpn_service import build_public_qr_url
from app.services.putaway_service import confirm_putaway


router = APIRouter()


@router.post(
    "/{lpn_barcode}/putaway/confirm",
    response_model=PutawayConfirmationResponse,
    operation_id="confirmLpnPutaway",
    summary="LPN 물리 적재 완료 및 재고 편입",
    description=(
        "작업자 2가 확정 로케이션에 도서를 적재한 뒤 완료 처리합니다. 신간은 "
        "묶음 재고, 승인된 중고·반품은 LPN 단품 재고에 편입하며 REJECT는 "
        "판매 재고 없이 C Zone 보관 완료로 기록합니다. 동일 LPN 재요청은 "
        "재고를 중복 증가시키지 않습니다."
    ),
    responses={
        401: {"description": "인증 토큰이 없거나 유효하지 않음"},
        403: {"description": "WMS 작업자 권한이 없음"},
        404: {"description": "입고된 LPN을 찾을 수 없음"},
        409: {"description": "적재 준비 미완료 또는 검수·로케이션 상태 충돌"},
        500: {"description": "LPN에 연결된 수명주기 데이터가 유실됨"},
    },
)
def confirm_lpn_putaway(
    lpn_barcode: str = Path(
        min_length=1,
        description="적재 완료할 물리 도서의 LPN 바코드",
        examples=["LPN-12345678123456781234567812345678"],
    ),
    session: Session = Depends(get_session),
    _: User = Depends(require_wms_operator),
) -> PutawayConfirmationResponse:
    try:
        result = confirm_putaway(
            session=session,
            lpn_barcode=lpn_barcode,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return PutawayConfirmationResponse(
        lpn_barcode=result.inbound_item.lpn_barcode or lpn_barcode,
        inbound_item_id=result.inbound_item.id,
        putaway_job_id=result.putaway_job.id,
        putaway_status=result.putaway_job.status,
        condition_grade=result.inbound_item.condition_grade,
        location=LpnLocationDetail(
            id=result.location.id,
            barcode=result.location.barcode,
            zone=result.location.zone,
            rack=result.location.rack,
            shelf=result.location.shelf,
        ),
        inventory_kind=result.inventory_kind,
        inventory_id=result.inventory_id,
        stock_changed=result.stock_changed,
    )


@router.get(
    "/{lpn_barcode}/putaway",
    response_model=LpnPutawayResponse,
    operation_id="getLpnPutawayInstruction",
    summary="LPN 기반 적재 지시 조회",
    description=(
        "작업자 2가 스캔한 LPN을 기준으로 신간·중고·반품 도서의 확정 등급과 "
        "적재할 Zone, Rack, Shelf를 조회합니다. 검수 또는 신간 패스트트랙을 "
        "통해 로케이션이 확정된 LPN만 조회할 수 있습니다."
    ),
    responses={
        401: {"description": "인증 토큰이 없거나 유효하지 않음"},
        403: {"description": "WMS 작업자 권한이 없음"},
        404: {"description": "입고된 LPN을 찾을 수 없음"},
        409: {"description": "검수 또는 로케이션 배정이 아직 완료되지 않음"},
        500: {"description": "LPN에 연결된 입고·도서·로케이션 데이터가 유실됨"},
    },
)
def get_lpn_putaway_instruction(
    lpn_barcode: str = Path(
        min_length=1,
        description="적재 지시를 조회할 물리 도서의 LPN 바코드",
        examples=["LPN-12345678123456781234567812345678"],
    ),
    session: Session = Depends(get_session),
    _: User = Depends(require_wms_operator),
) -> LpnPutawayResponse:
    inbound_item = session.exec(
        select(InboundItem).where(
            InboundItem.lpn_barcode == lpn_barcode,
        )
    ).first()
    if inbound_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inbound LPN item not found",
        )

    putaway_job = session.exec(
        select(PutawayJob).where(
            PutawayJob.inbound_item_id == inbound_item.id,
        )
    ).first()
    if putaway_job is None or inbound_item.condition_grade is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LPN is not ready for putaway",
        )

    inbound_job = session.get(InboundJob, inbound_item.inbound_job_id)
    book = session.get(Book, inbound_item.book_id)
    location = session.get(Location, putaway_job.location_id)
    if inbound_job is None or book is None or location is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LPN putaway references missing lifecycle data",
        )

    return LpnPutawayResponse(
        lpn_barcode=inbound_item.lpn_barcode or lpn_barcode,
        inbound_item_id=inbound_item.id,
        inbound_type=inbound_job.inbound_type,
        book=LpnPutawayBookDetail(
            id=book.id,
            isbn=book.isbn,
            title=book.title,
            category=book.category,
        ),
        condition_grade=inbound_item.condition_grade,
        putaway_job_id=putaway_job.id,
        putaway_status=putaway_job.status,
        location=LpnLocationDetail(
            id=location.id,
            barcode=location.barcode,
            zone=location.zone,
            rack=location.rack,
            shelf=location.shelf,
        ),
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
