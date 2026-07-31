from decimal import Decimal
from unittest.mock import MagicMock

from app.models.wms import Book, BookCategory
from app.services.aladin_book_service import AladinBookMetadata
from app.services.book_registration_service import register_book_by_isbn


def _exec_result(first_value=None):
    result = MagicMock()
    result.first.return_value = first_value
    return result


def test_returns_existing_book_without_calling_aladin():
    existing_book = Book(
        isbn="9788912345678",
        title="기존 도서",
        publisher="기존 출판사",
        category=BookCategory.NOVEL,
        base_price=Decimal("15000"),
    )
    session = MagicMock()
    session.exec.return_value = _exec_result(existing_book)
    metadata_lookup = MagicMock()

    result = register_book_by_isbn(
        session,
        "978-8912345678",
        metadata_lookup=metadata_lookup,
    )

    assert result.book is existing_book
    assert result.created is False
    metadata_lookup.assert_not_called()
    session.add.assert_not_called()


def test_registers_aladin_metadata_after_isbn_lock_and_recheck():
    session = MagicMock()
    session.exec.side_effect = [
        _exec_result(),
        MagicMock(),
        _exec_result(),
    ]
    metadata_lookup = MagicMock(
        return_value=AladinBookMetadata(
            isbn="9788912345678",
            title="신규 도서",
            publisher="신규 출판사",
            base_price=Decimal("18000"),
            category_id=50919,
            category_name="국내도서>소설/시/희곡>한국소설",
            category=BookCategory.NOVEL,
        )
    )

    result = register_book_by_isbn(
        session,
        "9788912345678",
        metadata_lookup=metadata_lookup,
    )

    assert result.created is True
    assert result.book.isbn == "9788912345678"
    assert result.book.title == "신규 도서"
    assert result.book.publisher == "신규 출판사"
    assert result.book.base_price == Decimal("18000")
    assert result.book.category == BookCategory.NOVEL
    session.add.assert_called_once_with(result.book)
    session.flush.assert_called_once()


def test_returns_book_created_by_concurrent_request_after_lock():
    concurrent_book = Book(
        isbn="9788912345678",
        title="동시 등록 도서",
        publisher="동시 등록 출판사",
        category=BookCategory.NOVEL,
        base_price=Decimal("17000"),
    )
    session = MagicMock()
    session.exec.side_effect = [
        _exec_result(),
        MagicMock(),
        _exec_result(concurrent_book),
    ]
    metadata_lookup = MagicMock(
        return_value=AladinBookMetadata(
            isbn="9788912345678",
            title="알라딘 도서",
            publisher="알라딘 출판사",
            base_price=Decimal("17000"),
            category_id=50919,
            category_name="국내도서>소설/시/희곡>한국소설",
            category=BookCategory.NOVEL,
        )
    )

    result = register_book_by_isbn(
        session,
        "9788912345678",
        metadata_lookup=metadata_lookup,
    )

    assert result.book is concurrent_book
    assert result.created is False
    session.add.assert_not_called()
