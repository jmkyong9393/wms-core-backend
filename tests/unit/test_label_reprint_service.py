from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.models.wms import (
    ConditionGrade,
    UsedInventoryStatus,
)
from app.domains.lpn.schemas.label import LabelType
from app.domains.lpn import label_reprint_service


class FakeResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class FakeSession:
    def __init__(self, results):
        self.results = iter(results)

    def exec(self, _statement):
        return FakeResult(next(self.results))


def build_inbound_item():
    return SimpleNamespace(
        lpn_barcode="LPN-TEST-0001",
        certificate_token="test-certificate-token",
    )


def build_inventory_item(
    *,
    status=UsedInventoryStatus.AVAILABLE,
    ubci_score=Decimal("91.50"),
):
    return SimpleNamespace(
        lpn_barcode="LPN-TEST-0001",
        condition_grade=ConditionGrade.EXCELLENT,
        ubci_score=ubci_score,
        status=status,
        return_job_id=UUID(
            "00000000-0000-4000-8000-000000000010"
        ),
    )


def build_return_job(
    final_report: str | None = (
        '{"message": "AI inspection completed."}'
    ),
):
    return SimpleNamespace(
        final_report=final_report,
    )


def test_builds_lpn_reprint_zpl_from_inbound_item(
    monkeypatch,
):
    captured_arguments = {}

    def fake_build_lpn_label_zpl(
        *,
        lpn_barcode,
        certificate_token,
    ):
        captured_arguments["lpn_barcode"] = lpn_barcode
        captured_arguments["certificate_token"] = (
            certificate_token
        )
        return "^XA^FDLPN^FS^XZ"

    monkeypatch.setattr(
        label_reprint_service,
        "build_lpn_label_zpl",
        fake_build_lpn_label_zpl,
    )

    result = label_reprint_service.build_label_reprint_zpl(
        session=FakeSession(
            [
                build_inbound_item(),
                None,
            ]
        ),
        lpn_barcode="LPN-TEST-0001",
        label_type=LabelType.LPN,
    )

    assert result == "^XA^FDLPN^FS^XZ"
    assert captured_arguments == {
        "lpn_barcode": "LPN-TEST-0001",
        "certificate_token": "test-certificate-token",
    }


def test_builds_ubci_reprint_zpl_from_sellable_inventory(
    monkeypatch,
):
    captured_arguments = {}

    def fake_build_ubci_label_zpl(
        *,
        lpn_barcode,
        certificate_token,
        condition_grade,
        ubci_score,
    ):
        captured_arguments.update(
            {
                "lpn_barcode": lpn_barcode,
                "certificate_token": certificate_token,
                "condition_grade": condition_grade,
                "ubci_score": ubci_score,
            }
        )
        return "^XA^FDUBCI^FS^XZ"

    monkeypatch.setattr(
        label_reprint_service,
        "build_ubci_label_zpl",
        fake_build_ubci_label_zpl,
    )

    result = label_reprint_service.build_label_reprint_zpl(
        session=FakeSession(
            [
                build_inbound_item(),
                build_inventory_item(),
                build_return_job(),
            ]
        ),
        lpn_barcode="LPN-TEST-0001",
        label_type=LabelType.UBCI,
    )

    assert result == "^XA^FDUBCI^FS^XZ"
    assert captured_arguments == {
        "lpn_barcode": "LPN-TEST-0001",
        "certificate_token": "test-certificate-token",
        "condition_grade": "EXCELLENT",
        "ubci_score": Decimal("91.50"),
    }


def test_rejects_unknown_lpn_reprint_request():
    with pytest.raises(HTTPException) as exc_info:
        label_reprint_service.build_label_reprint_zpl(
            session=FakeSession([None]),
            lpn_barcode="LPN-UNKNOWN",
            label_type=LabelType.LPN,
        )

    assert exc_info.value.status_code == 404


def test_rejects_ubci_reprint_before_inventory_is_created():
    with pytest.raises(HTTPException) as exc_info:
        label_reprint_service.build_label_reprint_zpl(
            session=FakeSession(
                [
                    build_inbound_item(),
                    None,
                ]
            ),
            lpn_barcode="LPN-TEST-0001",
            label_type=LabelType.UBCI,
        )

    assert exc_info.value.status_code == 409


def test_rejects_lpn_reprint_for_shipped_item():
    with pytest.raises(HTTPException) as exc_info:
        label_reprint_service.build_label_reprint_zpl(
            session=FakeSession(
                [
                    build_inbound_item(),
                    build_inventory_item(
                        status=UsedInventoryStatus.SHIPPED,
                    ),
                ]
            ),
            lpn_barcode="LPN-TEST-0001",
            label_type=LabelType.LPN,
        )

    assert exc_info.value.status_code == 409


def test_rejects_ubci_reprint_without_confirmed_score():
    with pytest.raises(HTTPException) as exc_info:
        label_reprint_service.build_label_reprint_zpl(
            session=FakeSession(
                [
                    build_inbound_item(),
                    build_inventory_item(ubci_score=None),
                ]
            ),
            lpn_barcode="LPN-TEST-0001",
            label_type=LabelType.UBCI,
        )

    assert exc_info.value.status_code == 409


def test_rejects_ubci_reprint_until_public_certificate_is_ready():
    with pytest.raises(HTTPException) as exc_info:
        label_reprint_service.build_label_reprint_zpl(
            session=FakeSession(
                [
                    build_inbound_item(),
                    build_inventory_item(),
                    build_return_job(final_report=None),
                ]
            ),
            lpn_barcode="LPN-TEST-0001",
            label_type=LabelType.UBCI,
        )

    assert exc_info.value.status_code == 409