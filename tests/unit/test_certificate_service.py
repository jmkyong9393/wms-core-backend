from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.wms import (
    Book,
    BookCategory,
    ConditionGrade,
    InboundItem,
    InventoryUsedItem,
    InspectionMode,
    ReturnJob,
)
from app.services.certificate_service import (
    extract_report_summary,
    get_certificate_by_token,
)


class _QueryResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _CertificateSession:
    def __init__(self, query_results, book):
        self.query_results = iter(query_results)
        self.book = book

    def exec(self, _statement):
        return _QueryResult(next(self.query_results))

    def get(self, _model, _identifier):
        return self.book


@pytest.mark.parametrize(
    ("final_report", "expected"),
    [
        (
            '{"message": "경미한 모서리 찍힘이 있습니다.", '
            '"result": "INSPECTION_COMPLETED"}',
            "경미한 모서리 찍힘이 있습니다.",
        ),
        (
            '{"result": "INSPECTION_COMPLETED"}',
            "INSPECTION_COMPLETED",
        ),
        (
            "전체 상태가 우수합니다.",
            "전체 상태가 우수합니다.",
        ),
        (None, None),
        ("  ", None),
        ('{"defects": []}', None),
    ],
)
def test_extract_report_summary(final_report, expected):
    assert extract_report_summary(final_report) == expected


def test_get_certificate_by_token_returns_public_certificate():
    book = Book(
        title="사피엔스",
        isbn="9781234567890",
        publisher="김영사",
        category=BookCategory.HUMANITIES,
    )
    inbound_item = InboundItem(
        inbound_job_id=uuid4(),
        book_id=book.id,
        quantity=1,
        certificate_token="public-certificate-token",
    )
    inspected_at = datetime(2026, 7, 27, 10, 0, 0)
    return_job = ReturnJob(
        tenant_id=uuid4(),
        book_id=book.id,
        inbound_item_id=inbound_item.id,
        mode=InspectionMode.USED_PURCHASE,
        ubci_score=Decimal("91.50"),
        condition_grade=ConditionGrade.EXCELLENT,
        final_report='{"message": "경미한 모서리 찍힘이 있습니다."}',
        updated_at=inspected_at,
    )
    inventory_item = InventoryUsedItem(
        book_id=book.id,
        location_id=uuid4(),
        return_job_id=return_job.id,
        lpn_barcode="LPN-TEST-001",
        condition_grade=ConditionGrade.EXCELLENT,
    )
    session = _CertificateSession(
        [inbound_item, return_job, inventory_item],
        book,
    )

    response = get_certificate_by_token(
        session,
        "public-certificate-token",
    )

    assert response.book.title == "사피엔스"
    assert response.book.isbn == "9781234567890"
    assert response.book.publisher == "김영사"
    assert response.condition_grade == ConditionGrade.EXCELLENT
    assert response.report_summary == "경미한 모서리 찍힘이 있습니다."
    assert response.inspected_at == inspected_at
    assert response.ubci_score == Decimal("91.50")
    assert response.model_dump()["ubci_score"] == Decimal("91.50")


@pytest.mark.parametrize(
    "query_results",
    [
        [None],
        [
            InboundItem(
                inbound_job_id=uuid4(),
                book_id=uuid4(),
                quantity=1,
                certificate_token="public-certificate-token",
            ),
            None,
        ],
    ],
)
def test_get_certificate_by_token_hides_missing_certificate_details(
    query_results,
):
    session = _CertificateSession(query_results, book=None)

    with pytest.raises(HTTPException) as exc_info:
        get_certificate_by_token(session, "public-certificate-token")

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Certificate not found"
