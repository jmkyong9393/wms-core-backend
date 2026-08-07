from dataclasses import dataclass
from typing import Callable

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.wms import Book
from app.services.aladin_book_service import (
    AladinBookMetadata,
    lookup_aladin_book_by_isbn,
    normalize_isbn,
)


@dataclass(frozen=True)
class BookRegistrationResult:
    book: Book
    created: bool


def register_book_by_isbn(
    session: Session,
    isbn: str,
    *,
    metadata_lookup: Callable[[str], AladinBookMetadata] = (
        lookup_aladin_book_by_isbn
    ),
) -> BookRegistrationResult:
    normalized_isbn = normalize_isbn(isbn)
    existing_book = _find_book_by_isbn(session, normalized_isbn)
    if existing_book is not None:
        return BookRegistrationResult(book=existing_book, created=False)

    metadata = metadata_lookup(normalized_isbn)
    canonical_isbn = normalize_isbn(metadata.isbn)

    # 외부 API 호출 뒤 잠금을 획득하여 네트워크 대기 중 DB Lock 점유를 피한다.
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:isbn, 0)"
            ")"
        ).bindparams(isbn=canonical_isbn)
    )

    existing_book = _find_book_by_isbn(session, canonical_isbn)
    if existing_book is not None:
        return BookRegistrationResult(book=existing_book, created=False)

    book = Book(
        isbn=canonical_isbn,
        title=metadata.title,
        publisher=metadata.publisher,
        cover_image_url=metadata.cover_image_url,
        category=metadata.category,
        base_price=metadata.base_price,
    )
    session.add(book)
    session.flush()
    return BookRegistrationResult(book=book, created=True)


def _find_book_by_isbn(session: Session, isbn: str) -> Book | None:
    return session.exec(select(Book).where(Book.isbn == isbn)).first()
