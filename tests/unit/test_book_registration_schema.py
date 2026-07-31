from app.api.routes.books import (
    BookRegistrationRequest,
    BookRegistrationResponse,
)
from app.models.wms import BookCategory


def test_book_registration_request_accepts_scanned_isbn():
    request = BookRegistrationRequest(isbn="978-8912345678")

    assert request.isbn == "978-8912345678"


def test_book_registration_response_exposes_registration_result():
    response_schema = BookRegistrationResponse.model_json_schema()

    assert {
        "book_id",
        "isbn",
        "title",
        "original_price",
        "publisher",
        "category",
        "created",
    }.issubset(response_schema["properties"])
    assert BookCategory.NOVEL.value in response_schema["$defs"]["BookCategory"]["enum"]
