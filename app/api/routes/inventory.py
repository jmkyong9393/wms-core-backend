from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book, ConditionGrade, Inventory, InventoryUsedItem, Location

router = APIRouter()
v1_router = APIRouter()


class InventoryBookResponse(BaseModel):
    title: str
    isbn: str | None = None


class InventoryListItemResponse(BaseModel):
    id: UUID
    book: InventoryBookResponse
    grade: ConditionGrade
    zone: str
    quantity: int
    date: datetime

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


@v1_router.get("", response_model=List[InventoryListItemResponse])
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
                grade=ConditionGrade.MINT,
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
                grade=used_item.condition_grade,
                zone=_format_location(location),
                quantity=1,
                date=used_item.updated_at,
            )
        )

    return sorted(inventory_items, key=lambda item: item.date, reverse=True)
