from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from app.api.routes import inspection_inventory
from app.models.wms import ConditionGrade
from app.schemas.label import LabelPrintStatus


def build_inbound_item():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        certificate_token="test-certificate-token",
    )


def build_inventory_item():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000002"),
        lpn_barcode="LPN-TEST-0001",
        condition_grade=ConditionGrade.EXCELLENT,
        ubci_score=Decimal("91.50"),
    )


def test_returns_sent_when_ubci_label_is_transmitted(
    monkeypatch,
):
    inbound_item = build_inbound_item()
    inventory_item = build_inventory_item()
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
        return "^XA^XZ"

    def fake_send_zpl_to_label_printer(_zpl):
        return SimpleNamespace(skipped=False)

    monkeypatch.setattr(
        inspection_inventory,
        "build_ubci_label_zpl",
        fake_build_ubci_label_zpl,
    )
    monkeypatch.setattr(
        inspection_inventory,
        "send_zpl_to_label_printer",
        fake_send_zpl_to_label_printer,
    )

    label_print_status, label_print_error = (
        inspection_inventory._try_print_ubci_label(
            inbound_item=inbound_item,
            inventory_item=inventory_item,
        )
    )

    assert captured_arguments == {
        "lpn_barcode": "LPN-TEST-0001",
        "certificate_token": "test-certificate-token",
        "condition_grade": "EXCELLENT",
        "ubci_score": Decimal("91.50"),
    }
    assert label_print_status == LabelPrintStatus.SENT
    assert label_print_error is None


def test_returns_skipped_when_ubci_printer_is_disabled(
    monkeypatch,
):
    inbound_item = build_inbound_item()
    inventory_item = build_inventory_item()

    def fake_send_zpl_to_label_printer(_zpl):
        return SimpleNamespace(skipped=True)

    monkeypatch.setattr(
        inspection_inventory,
        "send_zpl_to_label_printer",
        fake_send_zpl_to_label_printer,
    )

    label_print_status, label_print_error = (
        inspection_inventory._try_print_ubci_label(
            inbound_item=inbound_item,
            inventory_item=inventory_item,
        )
    )

    assert label_print_status == LabelPrintStatus.SKIPPED
    assert label_print_error is None


def test_returns_failed_without_raising_when_ubci_print_fails(
    monkeypatch,
):
    inbound_item = build_inbound_item()
    inventory_item = build_inventory_item()

    def fail_to_send_zpl(_zpl):
        raise OSError("Printer connection refused")

    monkeypatch.setattr(
        inspection_inventory,
        "send_zpl_to_label_printer",
        fail_to_send_zpl,
    )

    label_print_status, label_print_error = (
        inspection_inventory._try_print_ubci_label(
            inbound_item=inbound_item,
            inventory_item=inventory_item,
        )
    )

    assert label_print_status == LabelPrintStatus.FAILED
    assert label_print_error == (
        "UBCI 라벨 출력에 실패했습니다. 수동 출력이 필요합니다."
    )