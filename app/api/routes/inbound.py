from datetime import datetime
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, text, update
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    Inventory,
    InventoryLog,
    InventoryTransactionType,
    Location,
    StandardSize,
)

router = APIRouter()


def _lock_inbound_isbns(
    session: Session,
    items: List["NewStockInboundItemRequest"],
) -> None:
    # 아직 생성되지 않은 Book도 ISBN 단위로 직렬화하고, 정렬로 교착을 방지한다.
    for isbn in sorted({item.isbn for item in items}):
        session.exec(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:isbn, 0)"
                ")"
            ).bindparams(isbn=isbn)
        )


class NewStockInboundItemRequest(BaseModel):
    isbn: str = Field(min_length=10, max_length=13)
    title: str = Field(min_length=1)
    publisher: str | None = None
    base_price: Decimal = Field(gt=0)
    standard_size: StandardSize | None = None
    thickness_mm: int | None = Field(default=None, gt=0)
    quantity: int = Field(gt=0)


class NewStockInboundRequest(BaseModel):
    supplier_name: str | None = None
    location_barcode: str = Field(min_length=1)
    items: List[NewStockInboundItemRequest] = Field(min_length=1)


class NewStockInboundItemResponse(BaseModel):
    book_id: UUID
    isbn: str
    title: str
    quantity: int
    inventory_id: UUID
    inventory_quantity: int


class NewStockInboundResponse(BaseModel):
    inbound_id: UUID
    inbound_type: InboundType
    status: InboundStatus
    location_id: UUID
    location_barcode: str
    total_quantity: int
    items: List[NewStockInboundItemResponse]


class InboundHistoryItemResponse(BaseModel):
    inbound_id: UUID
    inbound_type: InboundType
    supplier_name: str | None = None
    status: InboundStatus
    total_quantity: int
    date: datetime


@router.post("/new-stock", response_model=NewStockInboundResponse)
def create_new_stock_inbound(
    request: NewStockInboundRequest,
    session: Session = Depends(get_session),
):
    location = session.exec(
        select(Location).where(Location.barcode == request.location_barcode)
    ).first()
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location barcode not found",
        )
    if not location.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Location is inactive",
        )

    try:
        _lock_inbound_isbns(session, request.items)

        inbound_job = InboundJob(
            inbound_type=InboundType.NEW_STOCK,
            status=InboundStatus.COMPLETED,
            supplier_name=request.supplier_name,
        )
        session.add(inbound_job)
        session.flush()

        response_items: list[NewStockInboundItemResponse] = []
        total_quantity = 0

        for item in request.items:
            book = session.exec(
                select(Book).where(Book.isbn == item.isbn)
            ).first()
            if book is None:  # Book이 없다면 새로 등록도 해준다.
                book = Book(
                    isbn=item.isbn,
                    title=item.title,
                    publisher=item.publisher,
                    base_price=item.base_price,
                    standard_size=item.standard_size,
                    thickness_mm=item.thickness_mm,
                )
                session.add(book)
                session.flush()

            session.add(
                InboundItem(
                    inbound_job_id=inbound_job.id,
                    book_id=book.id,
                    quantity=item.quantity,
                )
            )

            inventory = session.exec(
                select(Inventory)
                .where(
                    Inventory.book_id == book.id,
                    Inventory.location_id == location.id,
                )
                .with_for_update()
            ).first()
            if inventory is None:
                inventory = Inventory(
                    book_id=book.id,
                    location_id=location.id,
                    quantity=0,
                )
                session.add(inventory)
                session.flush()

            now = datetime.utcnow()
            inventory.quantity += item.quantity
            inventory.updated_at = now
            session.exec(
                update(Book)
                .where(Book.id == book.id)
                .values(
                    virtual_stock=Book.virtual_stock + item.quantity,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )

            session.add(
                InventoryLog(
                    transaction_type=InventoryTransactionType.INBOUND,
                    book_id=book.id,
                    condition_grade=ConditionGrade.MINT,
                    quantity_change=item.quantity,
                    picked_location=location.barcode,
                )
            )

            total_quantity += item.quantity
            response_items.append(
                NewStockInboundItemResponse(
                    book_id=book.id,
                    isbn=book.isbn or item.isbn,
                    title=book.title,
                    quantity=item.quantity,
                    inventory_id=inventory.id,
                    inventory_quantity=inventory.quantity,
                )
            )

        session.commit()
    except Exception:
        session.rollback()
        raise

    return NewStockInboundResponse(
        inbound_id=inbound_job.id,
        inbound_type=inbound_job.inbound_type,
        status=inbound_job.status,
        location_id=location.id,
        location_barcode=location.barcode or request.location_barcode,
        total_quantity=total_quantity,
        items=response_items,
    )


@router.get("/history", response_model=List[InboundHistoryItemResponse])
def get_inbound_history(
    limit: int = Query(default=10, ge=1, le=100),
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
