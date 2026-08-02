from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class DynamicPricingResult:
    inventory_used_item_id: UUID
    lpn_barcode: str
    base_price: Decimal
    discount_rate: Decimal
    sale_price: Decimal
    pricing_changed: bool


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


def apply_dynamic_pricing_result(
    session: Session,
    *,
    lpn_barcode: str,
    discount_rate: Decimal,
    final_price: Decimal,
) -> DynamicPricingResult:
    inventory_item = session.exec(
        select(InventoryUsedItem)
        .where(InventoryUsedItem.lpn_barcode == lpn_barcode)
        .with_for_update()
    ).first()
    if inventory_item is None:
        raise PricingContextNotFoundError(
            f"LPN inventory item not found: {lpn_barcode}"
        )

    book = session.get(Book, inventory_item.book_id)
    if book is None:
        raise PricingContextIncompleteError(
            "LPN inventory item references a missing book"
        )
    if inventory_item.ubci_score is None:
        raise PricingContextIncompleteError(
            "LPN inventory item does not have a confirmed UBCI score"
        )
    if book.base_price <= 0:
        raise PricingContextIncompleteError(
            "Book does not have a positive base price"
        )
    if discount_rate < 0 or discount_rate >= 1:
        raise PricingContextIncompleteError(
            "Discount rate must be greater than or equal to 0 and less than 1"
        )
    if final_price <= 0 or final_price > book.base_price:
        raise PricingContextIncompleteError(
            "Final price must be greater than 0 and not exceed the base price"
        )

    pricing_changed = (
        inventory_item.discount_rate != discount_rate
        or inventory_item.sale_price != final_price
    )
    if pricing_changed:
        inventory_item.discount_rate = discount_rate
        inventory_item.sale_price = final_price
        inventory_item.updated_at = datetime.utcnow()
        session.add(inventory_item)

    return DynamicPricingResult(
        inventory_used_item_id=inventory_item.id,
        lpn_barcode=inventory_item.lpn_barcode,
        base_price=book.base_price,
        discount_rate=discount_rate,
        sale_price=final_price,
        pricing_changed=pricing_changed,
    )
