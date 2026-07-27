from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book, InboundItem, InboundJob, InboundStatus
from app.schemas.used_inbound import (
    UsedBookInboundRequest,
    UsedBookInboundResponse,
)
from app.services.lpn_service import (
    build_public_qr_url,
    generate_certificate_token,
    generate_lpn_barcode,
)


router = APIRouter()


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
        "입고 품목과 LPN을 반환합니다. 응답의 certificate_url은 라벨 QR에 "
        "인코딩할 공개 품질보증서 URL입니다."
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

            session.commit()
            return _build_response(existing_job, existing_item)

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
            lpn_barcode=generate_lpn_barcode(request_id),
            certificate_token=generate_certificate_token(),
        )
        session.add(inbound_item)
        session.commit()
        session.refresh(inbound_job)
        session.refresh(inbound_item)
    except Exception:
        session.rollback()
        raise

    return _build_response(inbound_job, inbound_item)
