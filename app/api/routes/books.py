from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.database import get_session
from app.domain.aladin_category_policy import (
    UnsupportedAladinCategoryError,
)
from app.models.wms import Book, BookCategory
from app.services.aladin_book_service import (
    AladinBookNotFoundError,
    AladinConfigurationError,
    AladinInvalidResponseError,
    AladinUpstreamError,
)
from app.services.book_registration_service import register_book_by_isbn

router = APIRouter()


class BookLookupResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "isbn": "9788912345678",
                "title": "해리포터와 마법사의 돌",
                "original_price": "15000.00",
                "publisher": "문학수첩",
                "category": "NOVEL",
            }
        }
    )

    isbn: str = Field(description="조회된 도서의 ISBN")
    title: str = Field(description="도서명")
    original_price: Decimal = Field(description="도서 기준 판매가")
    publisher: str | None = Field(default=None, description="출판사명")
    cover_image_url: str | None = Field(
        default=None,
        description="도서 표지 이미지 URL",
    )
    category: BookCategory = Field(description="로케이션 Rack 배정용 도서 카테고리")


class BookRegistrationRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"isbn": "9788912345678"}}
    )

    isbn: str = Field(
        min_length=10,
        max_length=17,
        description="스캔한 ISBN-10 또는 ISBN-13 바코드",
    )


class BookRegistrationResponse(BookLookupResponse):
    book_id: UUID = Field(description="등록되거나 조회된 도서 마스터 ID")
    created: bool = Field(
        description="이번 요청에서 도서 마스터가 새로 생성되었는지 여부"
    )


@router.post(
    "/register",
    response_model=BookRegistrationResponse,
    operation_id="registerBookByIsbn",
    summary="ISBN으로 도서 마스터 등록",
    description=(
        "스캔한 ISBN이 이미 등록되어 있으면 기존 도서를 반환합니다. "
        "미등록 ISBN은 알라딘 OpenAPI에서 제목, 출판사, 정가와 카테고리를 "
        "조회하고 WMS 내부 카테고리로 변환하여 도서 마스터에 등록합니다."
    ),
    responses={
        404: {"description": "알라딘에서 ISBN을 찾을 수 없음"},
        422: {
            "description": "ISBN 또는 카테고리를 WMS 정책으로 처리할 수 없음"
        },
        502: {"description": "알라딘 OpenAPI가 잘못된 응답을 반환함"},
        503: {"description": "알라딘 OpenAPI 설정 누락 또는 통신 실패"},
    },
)
def register_book(
    request: BookRegistrationRequest,
    session: Session = Depends(get_session),
) -> BookRegistrationResponse:
    try:
        result = register_book_by_isbn(session, request.isbn)
        session.commit()
        session.refresh(result.book)
    except (ValueError, UnsupportedAladinCategoryError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except AladinBookNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="알라딘에서 해당 ISBN의 도서를 찾을 수 없습니다.",
        ) from exc
    except AladinInvalidResponseError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="알라딘 도서 정보 응답을 처리할 수 없습니다.",
        ) from exc
    except (AladinConfigurationError, AladinUpstreamError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="알라딘 도서 정보 서비스를 사용할 수 없습니다.",
        ) from exc
    except Exception:
        session.rollback()
        raise

    book = result.book
    return BookRegistrationResponse(
        book_id=book.id,
        isbn=book.isbn or request.isbn,
        title=book.title,
        original_price=book.base_price,
        publisher=book.publisher,
        cover_image_url=book.cover_image_url,
        category=book.category,
        created=result.created,
    )


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
        cover_image_url=book.cover_image_url,
        category=book.category,
    )
