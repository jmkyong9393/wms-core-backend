from datetime import datetime
from typing import List, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book, ConditionGrade, Inventory, InventoryUsedItem, Location

router = APIRouter()
v1_router = APIRouter()


class InventoryBookResponse(BaseModel):
    title: str = Field(description="도서명")
    isbn: str | None = Field(default=None, description="도서 ISBN")


class InventoryListItemResponse(BaseModel):
    id: UUID = Field(description="묶음 재고 또는 중고 단품 재고 ID")
    book: InventoryBookResponse = Field(description="재고 도서 정보")
    stock_type: Literal["NEW_STOCK", "USED_ITEM"] = Field(
        description="신간 묶음 재고 또는 중고·반품 단품 재고 구분",
    )
    grade: ConditionGrade | None = Field(
        description="중고·반품 품질 등급. 신간은 null",
    )
    zone: str = Field(description="재고가 보관된 로케이션")
    quantity: int = Field(
        description="재고 수량. 중고 단품 재고는 항상 1",
    )
    date: datetime = Field(description="재고가 마지막으로 변경된 시각")

@router.get("/status")
def get_inventory_status():
    return {
        "total_books": 1500,
        "total_locations": 200,
        "recent_transactions": [
            {"type": "INBOUND", "quantity": 10},
            {"type": "OUTBOUND", "quantity": 2},
        ]
    }


def _format_location(location: Location) -> str:
    return location.barcode or f"{location.zone}-{location.rack}-{location.shelf}"


@v1_router.get(
    "",
    response_model=List[InventoryListItemResponse],
    operation_id="listInventory",
    summary="단품 및 묶음 재고 통합 조회",
    description=(
        "정상 신간 묶음 재고와 중고·반품 LPN 단품 재고를 하나의 목록으로 "
        "조회합니다. 이 API는 재고를 변경하지 않습니다."
    ),
)
def list_inventory(session: Session = Depends(get_session)):
    new_stock_rows = session.exec(
        select(Inventory, Book, Location)
        .join(Book, Inventory.book_id == Book.id)
        .join(Location, Inventory.location_id == Location.id)
        .where(Inventory.quantity > 0)
        .order_by(Inventory.updated_at.desc())
    ).all()

    used_item_rows = session.exec(
        select(InventoryUsedItem, Book, Location)
        .join(Book, InventoryUsedItem.book_id == Book.id)
        .join(Location, InventoryUsedItem.location_id == Location.id)
        .order_by(InventoryUsedItem.updated_at.desc())
    ).all()

    inventory_items: list[InventoryListItemResponse] = []

    for inventory, book, location in new_stock_rows:
        inventory_items.append(
            InventoryListItemResponse(
                id=inventory.id,
                book=InventoryBookResponse(title=book.title, isbn=book.isbn),
                stock_type="NEW_STOCK",
                grade=None,
                zone=_format_location(location),
                quantity=inventory.quantity,
                date=inventory.updated_at,
            )
        )

    for used_item, book, location in used_item_rows:
        inventory_items.append(
            InventoryListItemResponse(
                id=used_item.id,
                book=InventoryBookResponse(title=book.title, isbn=book.isbn),
                stock_type="USED_ITEM",
                grade=used_item.condition_grade,
                zone=_format_location(location),
                quantity=1,
                date=used_item.updated_at,
            )
        )

    return sorted(inventory_items, key=lambda item: item.date, reverse=True)
