from types import SimpleNamespace
from uuid import UUID

from app.domains.inbound import used_inbound


def build_inbound_item():
    return SimpleNamespace(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        lpn_barcode="LPN-TEST-0001",
        certificate_token="test-certificate-token",
    )


def test_returns_sent_when_initial_lpn_label_is_transmitted(
    monkeypatch,
):
    inbound_item = build_inbound_item()
    captured_zpl_arguments = {}

    def fake_build_lpn_label_zpl(
        *,
        lpn_barcode,
        certificate_token,
    ):
        captured_zpl_arguments["lpn_barcode"] = lpn_barcode
        captured_zpl_arguments["certificate_token"] = certificate_token
        return "^XA^XZ"

    def fake_send_zpl_to_label_printer(_zpl):
        return SimpleNamespace(skipped=False)

    monkeypatch.setattr(
        used_inbound,
        "build_lpn_label_zpl",
        fake_build_lpn_label_zpl,
    )
    monkeypatch.setattr(
        used_inbound,
        "send_zpl_to_label_printer",
        fake_send_zpl_to_label_printer,
    )

    label_print_status, label_print_error = used_inbound._try_print_initial_lpn_label(inbound_item)

    assert captured_zpl_arguments == {
        "lpn_barcode": "LPN-TEST-0001",
        "certificate_token": "test-certificate-token",
    }
    assert label_print_status == "SENT"
    assert label_print_error is None


def test_returns_skipped_when_label_printer_is_disabled(
    monkeypatch,
):
    inbound_item = build_inbound_item()

    def fake_send_zpl_to_label_printer(_zpl):
        return SimpleNamespace(skipped=True)

    monkeypatch.setattr(
        used_inbound,
        "send_zpl_to_label_printer",
        fake_send_zpl_to_label_printer,
    )

    label_print_status, label_print_error = used_inbound._try_print_initial_lpn_label(inbound_item)

    assert label_print_status == "SKIPPED"
    assert label_print_error is None


def test_returns_failed_without_raising_when_printer_send_fails(
    monkeypatch,
):
    inbound_item = build_inbound_item()

    def fail_to_send_zpl(_zpl):
        raise OSError("Printer connection refused")

    monkeypatch.setattr(
        used_inbound,
        "send_zpl_to_label_printer",
        fail_to_send_zpl,
    )

    label_print_status, label_print_error = used_inbound._try_print_initial_lpn_label(inbound_item)

    assert label_print_status == "FAILED"
    assert label_print_error == ("LPN 라벨 출력에 실패했습니다. 수동 출력이 필요합니다.")
