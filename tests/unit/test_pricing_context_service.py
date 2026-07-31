from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.wms import (
    Book,
    BookCategory,
    ConditionGrade,
    InventoryUsedItem,
)
from app.services.pricing_context_service import (
    PricingContextIncompleteError,
    PricingContextNotFoundError,
    apply_dynamic_pricing_result,
    get_dynamic_pricing_context,
)


def _session_returning(row):
    result = MagicMock()
    result.first.return_value = row
    session = MagicMock()
    session.exec.return_value = result
    return session


def _book(base_price=Decimal("18000.00")):
    return Book(
        isbn="9788912345678",
        title="가격 책정 대상 도서",
        publisher="테스트 출판사",
        category=BookCategory.NOVEL,
        base_price=base_price,
    )


def _inventory_item(ubci_score=Decimal("91.50")):
    return InventoryUsedItem(
        book_id=_book().id,
        location_id="00000000-0000-4000-8000-000000000003",
        lpn_barcode="LPN-12345678123456781234567812345678",
        ubci_score=ubci_score,
        condition_grade=ConditionGrade.EXCELLENT,
    )


def test_returns_dynamic_pricing_source_data_for_lpn():
    book = _book()
    inventory_item = _inventory_item()
    inventory_item.book_id = book.id

    context = get_dynamic_pricing_context(
        _session_returning((inventory_item, book)),
        inventory_item.lpn_barcode,
    )

    assert context.inventory_used_item_id == inventory_item.id
    assert context.lpn_barcode == inventory_item.lpn_barcode
    assert context.book_id == book.id
    assert context.isbn == "9788912345678"
    assert context.base_price == Decimal("18000.00")
    assert context.category == BookCategory.NOVEL
    assert context.ubci_score == Decimal("91.50")
    assert context.condition_grade == ConditionGrade.EXCELLENT


def test_rejects_unknown_lpn():
    with pytest.raises(PricingContextNotFoundError):
        get_dynamic_pricing_context(
            _session_returning(None),
            "LPN-UNKNOWN",
        )


def test_rejects_lpn_without_confirmed_ubci_score():
    with pytest.raises(PricingContextIncompleteError):
        get_dynamic_pricing_context(
            _session_returning((_inventory_item(ubci_score=None), _book())),
            "LPN-12345678123456781234567812345678",
        )


def test_rejects_book_without_positive_base_price():
    with pytest.raises(PricingContextIncompleteError):
        get_dynamic_pricing_context(
            _session_returning(
                (_inventory_item(), _book(base_price=Decimal("0")))
            ),
            "LPN-12345678123456781234567812345678",
        )


def test_saves_dynamic_pricing_result_on_lpn_inventory():
    book = _book()
    inventory_item = _inventory_item()
    inventory_item.book_id = book.id
    session = _session_returning(inventory_item)
    session.get.return_value = book

    result = apply_dynamic_pricing_result(
        session,
        lpn_barcode=inventory_item.lpn_barcode,
        discount_rate=Decimal("0.1500"),
        final_price=Decimal("15300.00"),
    )

    assert inventory_item.discount_rate == Decimal("0.1500")
    assert inventory_item.sale_price == Decimal("15300.00")
    assert result.pricing_changed is True
    session.add.assert_called_once_with(inventory_item)


def test_same_pricing_result_is_idempotent():
    book = _book()
    inventory_item = _inventory_item()
    inventory_item.book_id = book.id
    inventory_item.discount_rate = Decimal("0.1500")
    inventory_item.sale_price = Decimal("15300.00")
    session = _session_returning(inventory_item)
    session.get.return_value = book

    result = apply_dynamic_pricing_result(
        session,
        lpn_barcode=inventory_item.lpn_barcode,
        discount_rate=Decimal("0.1500"),
        final_price=Decimal("15300.00"),
    )

    assert result.pricing_changed is False
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("discount_rate", "final_price"),
    [
        (Decimal("-0.0001"), Decimal("15300.00")),
        (Decimal("1.0000"), Decimal("15300.00")),
        (Decimal("0.1500"), Decimal("0")),
        (Decimal("0.1500"), Decimal("18000.01")),
    ],
)
def test_rejects_pricing_result_outside_inventory_policy(
    discount_rate,
    final_price,
):
    book = _book()
    inventory_item = _inventory_item()
    inventory_item.book_id = book.id
    session = _session_returning(inventory_item)
    session.get.return_value = book

    with pytest.raises(PricingContextIncompleteError):
        apply_dynamic_pricing_result(
            session,
            lpn_barcode=inventory_item.lpn_barcode,
            discount_rate=discount_rate,
            final_price=final_price,
        )


def test_rejects_pricing_result_for_unknown_lpn():
    with pytest.raises(PricingContextNotFoundError):
        apply_dynamic_pricing_result(
            _session_returning(None),
            lpn_barcode="LPN-UNKNOWN",
            discount_rate=Decimal("0.1500"),
            final_price=Decimal("15300.00"),
        )
