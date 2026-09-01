from datetime import datetime
from typing import List
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlmodel import Session, col, select

from app.core.database import get_session
from app.domains.inbound.inventory_admission_service import admit_new_stock
from app.domains.inbound.location_assignment_service import (
    NoAvailableLocationError,
    assign_new_stock_location,
)
from app.domains.inbound.schemas.new_stock_inbound import (
    NewStockInboundRequest,
    NewStockInboundResponse,
)
from app.models.wms import (
    Book,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    Inventory,
    Location,
)

router = APIRouter()


def _lock_new_stock_request(session: Session, request_id: UUID) -> None:
    session.exec(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:request_id, 0))").bindparams(request_id=str(request_id))
    )


def _lock_new_stock_isbn(session: Session, isbn: str) -> None:
    session.exec(text("SELECT pg_advisory_xact_lock(hashtextextended(:isbn, 0))").bindparams(isbn=isbn))


def _build_new_stock_response(
    inbound_job: InboundJob,
    inbound_item: InboundItem,
    location: Location,
    inventory: Inventory,
) -> NewStockInboundResponse:
    return NewStockInboundResponse(
        inbound_id=inbound_job.id,
        inbound_item_id=inbound_item.id,
        inbound_type=inbound_job.inbound_type,
        status=inbound_job.status,
        book_id=inbound_item.book_id,
        received_quantity=inbound_item.quantity,
        location_id=location.id,
        location_barcode=location.barcode,
        inventory_id=inventory.id,
        inventory_quantity=inventory.quantity,
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
    summary="ISBN 기반 신간 묶음 입고 및 로케이션 확정",
    description=(
        "동일 ISBN의 신간을 수량 단위로 입고합니다. 기존 신간 재고가 있으면 "
        "같은 로케이션을 재사용하고, 최초 입고일 때만 카테고리 기반 A Zone "
        "로케이션을 확정합니다. 신간에는 LPN과 품질 등급을 발급하지 않습니다."
    ),
    responses={
        409: {"description": ("Idempotency-Key 충돌 또는 적재 가능한 로케이션 없음")},
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
            if existing_job is None or existing_book is None:
                raise RuntimeError("Existing new stock intake is missing lifecycle records")
            existing_inventory_query = (
                select(Inventory, Location)
                .join(Location, Inventory.location_id == Location.id)
                .where(
                    Inventory.book_id == existing_book.id,
                    Location.zone == "A",
                    Location.is_active.is_(True),
                )
            )
            if existing_item.location_id is not None:
                existing_inventory_query = existing_inventory_query.where(
                    Inventory.location_id == existing_item.location_id,
                )
            existing_inventory_row = session.exec(
                existing_inventory_query.order_by(
                    Inventory.created_at,
                    Inventory.id,
                )
            ).first()
            if existing_inventory_row is None:
                raise RuntimeError("Existing new stock inventory was not found")
            existing_inventory, existing_location = existing_inventory_row
            if (
                existing_book.isbn != request.isbn
                or existing_job.inbound_type != InboundType.NEW_STOCK
                or existing_job.supplier_name != request.supplier_name
                or existing_item.quantity != request.quantity
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key is already used by another request",
                )

            response = _build_new_stock_response(
                existing_job,
                existing_item,
                existing_location,
                existing_inventory,
            )
            session.commit()
            return response
        _lock_new_stock_isbn(session, request.isbn)
        book = session.exec(select(Book).where(Book.isbn == request.isbn)).first()
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
            quantity=request.quantity,
        )
        session.add(inbound_item)
        session.flush()

        location = assign_new_stock_location(
            session=session,
            book=book,
            quantity=request.quantity,
        )
        inbound_item.location_id = location.id
        session.add(inbound_item)
        inventory = admit_new_stock(
            session=session,
            inbound_item=inbound_item,
            book=book,
            location=location,
        )
        inbound_job.status = InboundStatus.COMPLETED
        session.commit()
        session.refresh(inbound_job)
        session.refresh(inbound_item)
        session.refresh(location)
        session.refresh(inventory)
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
        location,
        inventory,
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
    inbound_jobs = session.exec(select(InboundJob).order_by(col(InboundJob.created_at).desc()).limit(limit)).all()
    if not inbound_jobs:
        return []

    inbound_job_ids = [job.id for job in inbound_jobs]
    quantity_rows = session.exec(
        select(
            InboundItem.inbound_job_id,
            func.coalesce(func.sum(InboundItem.quantity), 0),
        )
        .where(col(InboundItem.inbound_job_id).in_(inbound_job_ids))
        .group_by(InboundItem.inbound_job_id)
    ).all()
    quantity_by_job_id = {inbound_job_id: int(total_quantity) for inbound_job_id, total_quantity in quantity_rows}

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
