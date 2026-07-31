from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlmodel import Session, select

from app.models.wms import (
    Book,
    BookCategory,
    ConditionGrade,
    InventoryUsedItem,
)


class PricingContextNotFoundError(LookupError):
    pass


class PricingContextIncompleteError(ValueError):
    pass


@dataclass(frozen=True)
class DynamicPricingContext:
    inventory_used_item_id: UUID
    lpn_barcode: str
    book_id: UUID
    isbn: str | None
    base_price: Decimal
    category: BookCategory
    ubci_score: Decimal
    condition_grade: ConditionGrade


def get_dynamic_pricing_context(
    session: Session,
    lpn_barcode: str,
) -> DynamicPricingContext:
    row = session.exec(
        select(InventoryUsedItem, Book)
        .join(Book, InventoryUsedItem.book_id == Book.id)
        .where(InventoryUsedItem.lpn_barcode == lpn_barcode)
    ).first()
    if row is None:
        raise PricingContextNotFoundError(
            f"LPN inventory item not found: {lpn_barcode}"
        )

    inventory_item, book = row
    if inventory_item.ubci_score is None:
        raise PricingContextIncompleteError(
            "LPN inventory item does not have a confirmed UBCI score"
        )
    if book.base_price <= 0:
        raise PricingContextIncompleteError(
            "Book does not have a positive base price"
        )

    return DynamicPricingContext(
        inventory_used_item_id=inventory_item.id,
        lpn_barcode=inventory_item.lpn_barcode,
        book_id=book.id,
        isbn=book.isbn,
        base_price=book.base_price,
        category=book.category,
        ubci_score=inventory_item.ubci_score,
        condition_grade=inventory_item.condition_grade,
    )
