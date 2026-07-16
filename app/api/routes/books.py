from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book

router = APIRouter()


class BookLookupResponse(BaseModel):
    isbn: str
    title: str
    original_price: Decimal
    publisher: str | None = None


@router.get("/{isbn}", response_model=BookLookupResponse)
def get_book_by_isbn(
    isbn: str,
    session: Session = Depends(get_session),
):
    book = session.exec(select(Book).where(Book.isbn == isbn)).first()
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="등록되지 않은 도서입니다.",
        )

    return BookLookupResponse(
        isbn=book.isbn or isbn,
        title=book.title,
        original_price=book.base_price,
        publisher=book.publisher,
    )
