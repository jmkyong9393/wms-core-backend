from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.config import settings
from app.domain.aladin_category_policy import map_aladin_category
from app.models.wms import BookCategory


class AladinBookServiceError(Exception):
    pass


class AladinConfigurationError(AladinBookServiceError):
    pass


class AladinBookNotFoundError(AladinBookServiceError):
    pass


class AladinUpstreamError(AladinBookServiceError):
    pass


class AladinInvalidResponseError(AladinBookServiceError):
    pass


@dataclass(frozen=True)
class AladinBookMetadata:
    isbn: str
    title: str
    publisher: str | None
    base_price: Decimal
    category_id: int | None
    category_name: str
    category: BookCategory


def lookup_aladin_book_by_isbn(
    isbn: str,
    *,
    ttb_key: str | None = None,
    client: httpx.Client | None = None,
) -> AladinBookMetadata:
    normalized_isbn = normalize_isbn(isbn)
    api_key = ttb_key if ttb_key is not None else settings.ALADIN_TTB_KEY
    if not api_key.strip():
        raise AladinConfigurationError("ALADIN_TTB_KEY is not configured")

    request_client = client or httpx.Client()
    owns_client = client is None
    try:
        try:
            response = request_client.get(
                f"{settings.ALADIN_API_BASE_URL.rstrip('/')}/ItemLookUp.aspx",
                params={
                    "ttbkey": api_key,
                    "itemIdType": (
                        "ISBN13" if len(normalized_isbn) == 13 else "ISBN"
                    ),
                    "ItemId": normalized_isbn,
                    "output": "js",
                    "Version": "20131101",
                },
                timeout=settings.ALADIN_REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as exc:
            raise AladinUpstreamError(
                "Failed to connect to Aladin OpenAPI"
            ) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise AladinUpstreamError(
                f"Aladin OpenAPI returned status {response.status_code}"
            )
        if response.status_code >= 400:
            raise AladinInvalidResponseError(
                f"Aladin OpenAPI rejected the request: {response.status_code}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise AladinInvalidResponseError(
                "Aladin OpenAPI response is not valid JSON"
            ) from exc

        return _parse_book_metadata(payload, fallback_isbn=normalized_isbn)
    finally:
        if owns_client:
            request_client.close()


def normalize_isbn(isbn: str) -> str:
    normalized_isbn = isbn.replace("-", "").strip()
    if (
        len(normalized_isbn) not in {10, 13}
        or not normalized_isbn.isdigit()
    ):
        raise ValueError("ISBN must contain 10 or 13 digits")
    return normalized_isbn


def _parse_book_metadata(
    payload: Any,
    *,
    fallback_isbn: str,
) -> AladinBookMetadata:
    if not isinstance(payload, dict):
        raise AladinInvalidResponseError(
            "Aladin OpenAPI response must be a JSON object"
        )
    if payload.get("errorCode") is not None:
        raise AladinInvalidResponseError(
            f"Aladin OpenAPI error: {payload.get('errorMessage', 'unknown')}"
        )

    items = payload.get("item")
    if not isinstance(items, list) or not items:
        raise AladinBookNotFoundError(
            f"Book was not found for ISBN {fallback_isbn}"
        )

    item = items[0]
    if not isinstance(item, dict):
        raise AladinInvalidResponseError(
            "Aladin OpenAPI item must be a JSON object"
        )

    title = _required_text(item, "title")
    category_name = _required_text(item, "categoryName")
    returned_isbn = str(
        item.get("isbn13") or item.get("isbn") or fallback_isbn
    ).strip()

    try:
        base_price = Decimal(str(item["priceStandard"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise AladinInvalidResponseError(
            "Aladin OpenAPI response has an invalid standard price"
        ) from exc
    if base_price <= 0:
        raise AladinInvalidResponseError(
            "Aladin OpenAPI standard price must be positive"
        )

    category_id = _optional_int(item.get("categoryId"))
    publisher_value = item.get("publisher")
    publisher = (
        str(publisher_value).strip()
        if publisher_value is not None and str(publisher_value).strip()
        else None
    )

    return AladinBookMetadata(
        isbn=returned_isbn,
        title=title,
        publisher=publisher,
        base_price=base_price,
        category_id=category_id,
        category_name=category_name,
        category=map_aladin_category(category_name),
    )


def _required_text(item: dict[str, Any], field_name: str) -> str:
    value = item.get(field_name)
    if value is None or not str(value).strip():
        raise AladinInvalidResponseError(
            f"Aladin OpenAPI response is missing {field_name}"
        )
    return str(value).strip()


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AladinInvalidResponseError(
            "Aladin OpenAPI response has an invalid categoryId"
        ) from exc
