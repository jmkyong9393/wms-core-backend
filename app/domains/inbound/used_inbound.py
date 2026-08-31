from uuid import UUID, uuid4
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book, InboundItem, InboundJob, InboundStatus
from app.domains.lpn.schemas.label import LabelPrintStatus
from app.domains.inbound.schemas.used_inbound import (
    UsedBookInboundRequest,
    UsedBookInboundResponse,
)
from app.domains.lpn.label_printer_service import (
    send_zpl_to_label_printer,
)
from app.domains.lpn.lpn_service import (
    build_label_scan_qr_url,
    build_public_qr_url,
    generate_certificate_token,
    generate_lpn_barcode,
)
from app.domains.lpn.zpl_label_service import build_lpn_label_zpl

router = APIRouter()

logger = logging.getLogger(__name__)


def _try_print_initial_lpn_label(
    inbound_item: InboundItem,
) -> tuple[LabelPrintStatus, str | None]:
    """
    최초 입고가 DB에 저장된 뒤 LPN QR 라벨 전송을 시도한다.

    프린터 오류는 이미 완료된 입고 트랜잭션을 롤백하지 않는다.
    오류 상세는 서버 로그에 남기고, 응답에는 작업자용 안내만 반환한다.
    """
    try:
        if inbound_item.lpn_barcode is None:
            raise RuntimeError(
                "Inbound item does not have an LPN barcode"
            )
        if inbound_item.certificate_token is None:
            raise RuntimeError(
                "Inbound item does not have a certificate token"
            )

        zpl = build_lpn_label_zpl(
            lpn_barcode=inbound_item.lpn_barcode,
            certificate_token=inbound_item.certificate_token,
        )
        print_result = send_zpl_to_label_printer(zpl)

        if print_result.skipped:
            return LabelPrintStatus.SKIPPED, None

        return LabelPrintStatus.SENT, None

    except Exception:
        logger.exception(
            "Failed to print initial LPN label. "
            "inbound_item_id=%s",
            inbound_item.id,
        )
        return (
            LabelPrintStatus.FAILED,
            "LPN 라벨 출력에 실패했습니다. 수동 출력이 필요합니다.",
        )

def _lock_intake_request(session: Session, request_id: UUID) -> None:
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:request_id, 0)"
            ")"
        ).bindparams(request_id=str(request_id))
    )


def _build_response(
    inbound_job: InboundJob,
    inbound_item: InboundItem,
    label_print_status: LabelPrintStatus = "SKIPPED",
    label_print_error: str | None = None,
) -> UsedBookInboundResponse:
    if inbound_item.lpn_barcode is None:
        raise RuntimeError("Used inbound item does not have an LPN barcode")
    if inbound_item.certificate_token is None:
        raise RuntimeError("Used inbound item does not have a certificate token")

    return UsedBookInboundResponse(
        inbound_id=inbound_job.id,
        inbound_item_id=inbound_item.id,
        inbound_type=inbound_job.inbound_type,
        status=inbound_job.status,
        book_id=inbound_item.book_id,
        lpn_barcode=inbound_item.lpn_barcode,
        certificate_url=build_public_qr_url(inbound_item.certificate_token),
        label_scan_url=build_label_scan_qr_url(
            inbound_item.certificate_token
        ),
        label_print_status=label_print_status,
        label_print_error=label_print_error,
    )


@router.post(
    "/used-item",
    response_model=UsedBookInboundResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createUsedBookInbound",
    summary="중고·반품 도서 입고 접수 및 LPN 발급",
    description=(
        "중고 매입 또는 고객 반품 도서 1권을 검수 대기 상태로 접수하고 "
        "물리적 단품 추적용 LPN을 발급합니다. 이 단계에서는 판매 가능 "
        "재고에 편입하지 않습니다. 동일 Idempotency-Key 재요청은 기존 "
        "입고 품목과 LPN을 반환합니다."
        "응답의 label_scan_url이 실제 물리 라벨 QR에 인코딩된다."
        "certificate_url은 소비자용 품질보증서 직접 조회 URL이다."
    ),
    responses={
        404: {"description": "도서 마스터를 찾을 수 없음"},
        409: {"description": "Idempotency-Key가 다른 입고 요청에 사용됨"},
    },
)
def create_used_book_inbound(
    request: UsedBookInboundRequest,
    idempotency_key: UUID | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="재요청 중복 입고를 방지하는 클라이언트 생성 UUID",
    ),
    session: Session = Depends(get_session),
) -> UsedBookInboundResponse:
    request_id = idempotency_key or uuid4()

    try:
        _lock_intake_request(session, request_id)

        existing_item = session.get(InboundItem, request_id)
        if existing_item is not None:
            existing_job = session.get(InboundJob, existing_item.inbound_job_id)
            if existing_job is None:
                raise RuntimeError("Inbound job for existing item was not found")

            if (
                existing_item.book_id != request.book_id
                or existing_job.inbound_type != request.inbound_type
                or existing_job.supplier_name != request.supplier_name
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key is already used by another request",
                )

            response = _build_response(
                existing_job,
                existing_item,
            )
            session.commit()
            return response

        book = session.exec(
            select(Book).where(Book.id == request.book_id)
        ).first()
        if book is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Book not found",
            )

        inbound_job = InboundJob(
            inbound_type=request.inbound_type,
            status=InboundStatus.CHECKING,
            supplier_name=request.supplier_name,
        )
        session.add(inbound_job)
        session.flush()

        inbound_item = InboundItem(
            id=request_id,
            inbound_job_id=inbound_job.id,
            book_id=book.id,
            quantity=1,
            lpn_barcode=generate_lpn_barcode(session),
            certificate_token=generate_certificate_token(),
        )
        session.add(inbound_item)
        session.commit()
        session.refresh(inbound_job)
        session.refresh(inbound_item)
    except Exception:
        session.rollback()
        raise

    # DB commit이 끝난 뒤에만 실제 프린터 전송을 시도한다.
    # 출력 실패는 입고·LPN 발급 결과를 롤백하지 않는다.
    label_print_status, label_print_error = (
        _try_print_initial_lpn_label(inbound_item)
    )

    return _build_response(
        inbound_job,
        inbound_item,
        label_print_status=label_print_status,
        label_print_error=label_print_error,
    )
