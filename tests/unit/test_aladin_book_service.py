from decimal import Decimal

import httpx
import pytest

from app.services.aladin_book_service import (
    AladinBookNotFoundError,
    AladinConfigurationError,
    AladinInvalidResponseError,
    AladinUpstreamError,
    lookup_aladin_book_by_isbn,
)
from app.models.wms import BookCategory


def _client_with_response(
    *,
    status_code: int = 200,
    payload: object | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(status_code, request=request)
        return httpx.Response(status_code, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_looks_up_isbn13_and_normalizes_aladin_metadata():
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "item": [
                    {
                        "title": "테스트 도서",
                        "isbn13": "9788912345678",
                        "publisher": "테스트 출판사",
                        "priceStandard": 15000,
                        "categoryId": 50919,
                        "categoryName": "국내도서>소설/시/희곡>한국소설",
                    }
                ]
            },
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = lookup_aladin_book_by_isbn(
            "978-8912345678",
            ttb_key="test-key",
            client=client,
        )

    assert result.isbn == "9788912345678"
    assert result.title == "테스트 도서"
    assert result.publisher == "테스트 출판사"
    assert result.base_price == Decimal("15000")
    assert result.category_id == 50919
    assert result.category_name == "국내도서>소설/시/희곡>한국소설"
    assert result.category == BookCategory.NOVEL
    assert captured_request is not None
    assert captured_request.url.params["ttbkey"] == "test-key"
    assert captured_request.url.params["itemIdType"] == "ISBN13"
    assert captured_request.url.params["ItemId"] == "9788912345678"
    assert captured_request.url.params["output"] == "js"


def test_rejects_lookup_when_ttb_key_is_missing():
    with pytest.raises(AladinConfigurationError):
        lookup_aladin_book_by_isbn(
            "9788912345678",
            ttb_key="",
        )


def test_reports_book_not_found_for_empty_item_list():
    with _client_with_response(payload={"item": []}) as client:
        with pytest.raises(AladinBookNotFoundError):
            lookup_aladin_book_by_isbn(
                "9788912345678",
                ttb_key="test-key",
                client=client,
            )


def test_rejects_invalid_standard_price():
    with _client_with_response(
        payload={
            "item": [
                {
                    "title": "테스트 도서",
                    "priceStandard": "invalid",
                    "categoryName": "국내도서>소설",
                }
            ]
        }
    ) as client:
        with pytest.raises(AladinInvalidResponseError):
            lookup_aladin_book_by_isbn(
                "9788912345678",
                ttb_key="test-key",
                client=client,
            )


def test_treats_server_error_as_upstream_failure():
    with _client_with_response(status_code=503) as client:
        with pytest.raises(AladinUpstreamError):
            lookup_aladin_book_by_isbn(
                "9788912345678",
                ttb_key="test-key",
                client=client,
            )


def test_treats_network_error_as_upstream_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AladinUpstreamError):
            lookup_aladin_book_by_isbn(
                "9788912345678",
                ttb_key="test-key",
                client=client,
            )


@pytest.mark.parametrize("isbn", ["", "123", "ABCDEFGHIJKLM"])
def test_rejects_invalid_isbn(isbn):
    with pytest.raises(ValueError):
        lookup_aladin_book_by_isbn(
            isbn,
            ttb_key="test-key",
        )
