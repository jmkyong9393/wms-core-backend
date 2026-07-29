from datetime import datetime
from decimal import Decimal
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, text, update
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import (
    Book,
    BookCategory,
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
    isbn: str = Field(
        min_length=10,
        max_length=13,
        description="입고 도서 ISBN",
        examples=["9788912345678"],
    )
    title: str = Field(min_length=1, description="도서명")
    publisher: str | None = Field(default=None, description="출판사명")
    category: BookCategory = Field(description="로케이션 Rack 배정용 도서 카테고리")
    base_price: Decimal = Field(gt=0, description="도서 기준 판매가")
    standard_size: StandardSize | None = Field(
        default=None,
        description="3D Bin Packing용 도서 규격",
    )
    thickness_mm: int | None = Field(
        default=None,
        gt=0,
        description="도서 두께(mm)",
    )
    quantity: int = Field(gt=0, description="입고 수량")


class NewStockInboundRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "supplier_name": "교보문고",
                "location_barcode": "A-1-3",
                "items": [
                    {
                        "isbn": "9788912345678",
                        "title": "해리포터와 마법사의 돌",
                        "publisher": "문학수첩",
                        "category": "NOVEL",
                        "base_price": "15000.00",
                        "standard_size": "A5",
                        "thickness_mm": 20,
                        "quantity": 50,
                    }
                ],
            }
        }
    )

    supplier_name: str | None = Field(default=None, description="공급처명")
    location_barcode: str = Field(
        min_length=1,
        description="신간을 적재할 활성 로케이션 바코드",
    )
    items: List[NewStockInboundItemRequest] = Field(
        min_length=1,
        description="입고할 정상 신간 품목 목록",
    )


class NewStockInboundItemResponse(BaseModel):
    book_id: UUID = Field(description="도서 마스터 ID")
    isbn: str = Field(description="도서 ISBN")
    title: str = Field(description="도서명")
    quantity: int = Field(description="이번 요청에서 입고된 수량")
    inventory_id: UUID = Field(description="반영된 묶음 재고 ID")
    inventory_quantity: int = Field(description="입고 반영 후 로케이션 재고 수량")


class NewStockInboundResponse(BaseModel):
    inbound_id: UUID = Field(description="생성된 입고 작업 ID")
    inbound_type: InboundType = Field(description="입고 유형")
    status: InboundStatus = Field(description="입고 처리 상태")
    location_id: UUID = Field(description="입고 로케이션 ID")
    location_barcode: str = Field(description="입고 로케이션 바코드")
    total_quantity: int = Field(description="이번 입고 작업의 총수량")
    items: List[NewStockInboundItemResponse] = Field(description="품목별 반영 결과")


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
    operation_id="createNewStockInbound",
    summary="정상 신간 입고 및 묶음 재고 편입",
    description=(
        "정상 신간 입고 작업과 품목을 생성하고 로케이션별 묶음 재고를 "
        "증가시킵니다. 동일 ISBN의 동시 입고는 PostgreSQL Lock으로 직렬화합니다."
    ),
    responses={
        404: {"description": "입고 로케이션 바코드를 찾을 수 없음"},
        409: {"description": "입고 로케이션이 비활성 상태"},
    },
)
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
                    category=item.category,
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
                    condition_grade=ConditionGrade.NEW,
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
                    condition_grade=ConditionGrade.NEW,
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
