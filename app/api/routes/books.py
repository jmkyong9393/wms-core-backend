from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.wms import Book

router = APIRouter()


class BookLookupResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "isbn": "9788912345678",
                "title": "해리포터와 마법사의 돌",
                "original_price": "15000.00",
                "publisher": "문학수첩",
            }
        }
    )

    isbn: str = Field(description="조회된 도서의 ISBN")
    title: str = Field(description="도서명")
    original_price: Decimal = Field(description="도서 기준 판매가")
    publisher: str | None = Field(default=None, description="출판사명")


@router.get(
    "/{isbn}",
    response_model=BookLookupResponse,
    operation_id="getBookByIsbn",
    summary="ISBN으로 도서 기초 정보 조회",
    description="바코드로 스캔한 ISBN을 이용해 도서 마스터 정보를 조회합니다.",
    responses={
        404: {
            "description": "등록되지 않은 ISBN",
            "content": {
                "application/json": {
                    "example": {"detail": "등록되지 않은 도서입니다."}
                }
            },
        }
    },
)
def get_book_by_isbn(
    isbn: str = Path(
        min_length=10,
        max_length=13,
        description="조회할 10~13자리 ISBN 바코드",
        examples=["9788912345678"],
    ),
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
