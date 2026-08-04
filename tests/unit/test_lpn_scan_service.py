from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from app.models.wms import (
    ConditionGrade,
    InboundStatus,
    InboundType,
    RejectedItemStatus,
    ReturnJobStatus,
    UsedInventoryStatus,
)
from app.services import lpn_scan_service


class FakeResult:
    def __init__(self, first_value=None):
        self.first_value = first_value

    def first(self):
        return self.first_value


class FakeSession:
    def __init__(self, *, results, model_values):
        self.results = list(results)
        self.model_values = model_values

    def exec(self, _statement):
        return self.results.pop(0)

    def get(self, model, _id):
        return self.model_values.get(model)


def build_inbound_item():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        inbound_job_id=UUID(
            "00000000-0000-4000-8000-000000000002"
        ),
        book_id=UUID(
            "00000000-0000-4000-8000-000000000003"
        ),
        certificate_token="test-certificate-token",
        lpn_barcode="LPN-TEST-0001",
        condition_grade=None,
    )


def build_inbound_job():
    return SimpleNamespace(
        inbound_type=InboundType.USED_PURCHASE,
        status=InboundStatus.COMPLETED,
    )


def build_book():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000003"),
        isbn="9790000000001",
        title="LPN 스캔 테스트 도서",
        publisher="테스트 출판사",
    )


def build_location():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000004"),
        barcode="B-1-2",
        zone="B",
        rack="1",
        shelf="2",
    )


def test_returns_available_inventory_location_after_inspection():
    inbound_item = build_inbound_item()
    return_job = SimpleNamespace(
        status=ReturnJobStatus.APPROVED,
        condition_grade=ConditionGrade.EXCELLENT,
        ubci_score=Decimal("91.50"),
    )
    used_inventory_item = SimpleNamespace(
        location_id=UUID(
            "00000000-0000-4000-8000-000000000004"
        ),
        status=UsedInventoryStatus.AVAILABLE,
    )

    session = FakeSession(
        results=[
            FakeResult(inbound_item),
            FakeResult(return_job),
            FakeResult(used_inventory_item),
            FakeResult(None),
        ],
        model_values={
            lpn_scan_service.InboundJob: build_inbound_job(),
            lpn_scan_service.Book: build_book(),
            lpn_scan_service.Location: build_location(),
        },
    )

    result = lpn_scan_service.get_lpn_scan_detail(
        session=session,
        certificate_token="test-certificate-token",
    )

    assert result.lpn_barcode == "LPN-TEST-0001"
    assert result.final_grade == ConditionGrade.EXCELLENT
    assert result.ubci_score == Decimal("91.50")
    assert result.inventory_status == (
        UsedInventoryStatus.AVAILABLE
    )
    assert result.location is not None
    assert result.location.barcode == "B-1-2"
    assert result.requires_retake is False
    assert result.return_job_id is None


def test_marks_retake_required_before_inventory_is_created():
    inbound_item = build_inbound_item()
    return_job = SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000005"),
        status=ReturnJobStatus.RECHECK_REQUIRED,
        condition_grade=None,
        ubci_score=None,
    )

    session = FakeSession(
        results=[
            FakeResult(inbound_item),
            FakeResult(return_job),
            FakeResult(None),
            FakeResult(None),
        ],
        model_values={
            lpn_scan_service.InboundJob: build_inbound_job(),
            lpn_scan_service.Book: build_book(),
        },
    )

    result = lpn_scan_service.get_lpn_scan_detail(
        session=session,
        certificate_token="test-certificate-token",
    )

    assert result.requires_retake is True
    assert result.location is None
    assert result.inventory_status is None
    assert result.return_job_id == return_job.id


def test_returns_rejected_item_location():
    inbound_item = build_inbound_item()
    return_job = SimpleNamespace(
        status=ReturnJobStatus.REJECTED,
        condition_grade=ConditionGrade.REJECT,
        ubci_score=Decimal("40.00"),
    )
    rejected_item = SimpleNamespace(
        location_id=UUID(
            "00000000-0000-4000-8000-000000000004"
        ),
        status=RejectedItemStatus.REJECT_HOLD,
    )

    session = FakeSession(
        results=[
            FakeResult(inbound_item),
            FakeResult(return_job),
            FakeResult(None),
            FakeResult(rejected_item),
        ],
        model_values={
            lpn_scan_service.InboundJob: build_inbound_job(),
            lpn_scan_service.Book: build_book(),
            lpn_scan_service.Location: build_location(),
        },
    )

    result = lpn_scan_service.get_lpn_scan_detail(
        session=session,
        certificate_token="test-certificate-token",
    )

    assert result.final_grade == ConditionGrade.REJECT
    assert result.rejected_item_status == (
        RejectedItemStatus.REJECT_HOLD
    )
    assert result.location is not None
    assert result.location.barcode == "B-1-2"
    assert result.requires_retake is False