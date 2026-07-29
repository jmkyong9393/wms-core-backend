from datetime import datetime
from typing import List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    Location,
    PutawayJob,
)
from app.schemas.new_stock_inbound import (
    NewStockInboundRequest,
    NewStockInboundResponse,
)
from app.services.location_assignment_service import (
    NoAvailableLocationError,
    assign_putaway_location,
)
from app.services.lpn_service import (
    build_public_qr_url,
    generate_certificate_token,
    generate_lpn_barcode,
)

router = APIRouter()


def _lock_new_stock_request(session: Session, request_id: UUID) -> None:
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:request_id, 0)"
            ")"
        ).bindparams(request_id=str(request_id))
    )


def _lock_new_stock_isbn(session: Session, isbn: str) -> None:
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:isbn, 0)"
            ")"
        ).bindparams(isbn=isbn)
    )


def _build_new_stock_response(
    inbound_job: InboundJob,
    inbound_item: InboundItem,
    putaway_job: PutawayJob,
    location: Location,
) -> NewStockInboundResponse:
    if inbound_item.lpn_barcode is None:
        raise RuntimeError("New stock inbound item does not have an LPN barcode")
    if inbound_item.certificate_token is None:
        raise RuntimeError(
            "New stock inbound item does not have a certificate token"
        )
    if inbound_item.condition_grade != ConditionGrade.NEW:
        raise RuntimeError("New stock inbound item does not have NEW grade")

    return NewStockInboundResponse(
        inbound_id=inbound_job.id,
        inbound_item_id=inbound_item.id,
        inbound_type=inbound_job.inbound_type,
        status=inbound_job.status,
        book_id=inbound_item.book_id,
        condition_grade=inbound_item.condition_grade,
        lpn_barcode=inbound_item.lpn_barcode,
        certificate_url=build_public_qr_url(inbound_item.certificate_token),
        putaway_status=putaway_job.status,
        location_id=location.id,
        location_barcode=location.barcode,
    )


class InboundHistoryItemResponse(BaseModel):
    inbound_id: UUID = Field(description="입고 작업 ID")
    inbound_type: InboundType = Field(description="입고 유형")
    supplier_name: str | None = Field(default=None, description="공급처명")
    status: InboundStatus = Field(description="입고 처리 상태")
    total_quantity: int = Field(description="입고 작업의 총수량")
    date: datetime = Field(description="입고 작업 생성 시각")


@router.post(
    "/new-stock",
    response_model=NewStockInboundResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createNewStockInbound",
    summary="신간 단품 입고 접수, LPN 발급 및 로케이션 확정",
    description=(
        "신간 도서 1권에 LPN과 품질보증서 QR 경로를 발급하고 NEW 등급을 "
        "즉시 적용합니다. 카테고리 기반 로케이션을 확정하여 적재 대기 작업을 "
        "생성하며, 작업자가 실제 적재를 완료하기 전에는 판매 재고에 편입하지 "
        "않습니다. 동일 Idempotency-Key 재요청은 기존 결과를 반환합니다."
    ),
    responses={
        409: {
            "description": (
                "Idempotency-Key 충돌 또는 적재 가능한 로케이션 없음"
            )
        },
    },
)
def create_new_stock_inbound(
    request: NewStockInboundRequest,
    idempotency_key: UUID | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="재요청 중복 입고를 방지하는 클라이언트 생성 UUID",
    ),
    session: Session = Depends(get_session),
) -> NewStockInboundResponse:
    request_id = idempotency_key or uuid4()

    try:
        _lock_new_stock_request(session, request_id)

        existing_item = session.get(InboundItem, request_id)
        if existing_item is not None:
            existing_job = session.get(InboundJob, existing_item.inbound_job_id)
            existing_book = session.get(Book, existing_item.book_id)
            existing_putaway = session.exec(
                select(PutawayJob).where(
                    PutawayJob.inbound_item_id == existing_item.id
                )
            ).first()
            if (
                existing_job is None
                or existing_book is None
                or existing_putaway is None
            ):
                raise RuntimeError(
                    "Existing new stock intake is missing lifecycle records"
                )
            existing_location = session.get(
                Location,
                existing_putaway.location_id,
            )
            if existing_location is None:
                raise RuntimeError(
                    "Existing new stock putaway location was not found"
                )
            if (
                existing_book.isbn != request.isbn
                or existing_job.inbound_type != InboundType.NEW_STOCK
                or existing_job.supplier_name != request.supplier_name
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key is already used by another request",
                )

            session.commit()
            return _build_new_stock_response(
                existing_job,
                existing_item,
                existing_putaway,
                existing_location,
            )

        _lock_new_stock_isbn(session, request.isbn)
        book = session.exec(
            select(Book).where(Book.isbn == request.isbn)
        ).first()
        if book is None:
            book = Book(
                isbn=request.isbn,
                title=request.title,
                publisher=request.publisher,
                category=request.category,
                base_price=request.base_price,
                standard_size=request.standard_size,
                thickness_mm=request.thickness_mm,
            )
            session.add(book)
            session.flush()

        inbound_job = InboundJob(
            inbound_type=InboundType.NEW_STOCK,
            status=InboundStatus.RECEIVED,
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
            condition_grade=ConditionGrade.NEW,
        )
        session.add(inbound_item)
        session.flush()

        assignment = assign_putaway_location(
            session=session,
            inbound_item=inbound_item,
            book=book,
            grade=ConditionGrade.NEW,
        )
        session.commit()
        session.refresh(inbound_job)
        session.refresh(inbound_item)
        session.refresh(assignment.putaway_job)
        session.refresh(assignment.location)
    except NoAvailableLocationError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        session.rollback()
        raise

    return _build_new_stock_response(
        inbound_job,
        inbound_item,
        assignment.putaway_job,
        assignment.location,
    )


@router.get(
    "/history",
    response_model=List[InboundHistoryItemResponse],
    operation_id="getInboundHistory",
    summary="최근 입고 이력 조회",
    description="최근 입고 작업을 생성 시각 역순으로 조회합니다.",
)
def get_inbound_history(
    limit: int = Query(
        default=10,
        ge=1,
        le=100,
        description="조회할 최대 입고 작업 수",
    ),
    session: Session = Depends(get_session),
):
    inbound_jobs = session.exec(
        select(InboundJob).order_by(InboundJob.created_at.desc()).limit(limit)
    ).all()
    if not inbound_jobs:
        return []

    inbound_job_ids = [job.id for job in inbound_jobs]
    quantity_rows = session.exec(
        select(
            InboundItem.inbound_job_id,
            func.coalesce(func.sum(InboundItem.quantity), 0),
        )
        .where(InboundItem.inbound_job_id.in_(inbound_job_ids))
        .group_by(InboundItem.inbound_job_id)
    ).all()
    quantity_by_job_id = {
        inbound_job_id: int(total_quantity)
        for inbound_job_id, total_quantity in quantity_rows
    }

    return [
        InboundHistoryItemResponse(
            inbound_id=job.id,
            inbound_type=job.inbound_type,
            supplier_name=job.supplier_name,
            status=job.status,
            total_quantity=quantity_by_job_id.get(job.id, 0),
            date=job.created_at,
        )
        for job in inbound_jobs
    ]
