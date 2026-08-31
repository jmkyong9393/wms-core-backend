from decimal import Decimal

import pytest

from app.domains.lpn import (
    label_printer_service,
    zpl_label_service,
)


class FakePrinterSocket:
    def __init__(self):
        self.sent_payload = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def sendall(self, payload: bytes):
        self.sent_payload = payload


def test_build_lpn_label_uses_shared_scan_qr_url(
    monkeypatch,
):
    monkeypatch.setattr(
        zpl_label_service.settings,
        "PUBLIC_WEB_BASE_URL",
        "https://wms.example.com",
    )

    zpl = zpl_label_service.build_lpn_label_zpl(
        lpn_barcode="LPN-TEST-0001",
        certificate_token="test-certificate-token",
    )

    assert "^PW400" in zpl
    assert "^LL240" in zpl
    assert (
        "https://wms.example.com/"
        "scan/test-certificate-token"
    ) in zpl
    assert "LPN-TEST-0001" in zpl
    assert "^BQN" in zpl
    assert "^BC" not in zpl


def test_build_ubci_label_includes_grade_and_score(
    monkeypatch,
):
    monkeypatch.setattr(
        zpl_label_service.settings,
        "PUBLIC_WEB_BASE_URL",
        "https://wms.example.com",
    )

    zpl = zpl_label_service.build_ubci_label_zpl(
        lpn_barcode="LPN-TEST-0001",
        certificate_token="test-certificate-token",
        condition_grade="EXCELLENT",
        ubci_score=Decimal("91.50"),
    )

    assert "GRADE: EXCELLENT" in zpl
    assert "UBCI: 91.50" in zpl
    assert (
        "https://wms.example.com/"
        "scan/test-certificate-token"
    ) in zpl


def test_skips_printer_connection_when_printer_is_disabled(
    monkeypatch,
):
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_ENABLED",
        False,
    )

    result = (
        label_printer_service.send_zpl_to_label_printer(
            "^XA^XZ"
        )
    )

    assert result.sent is False
    assert result.skipped is True
    assert result.bytes_sent == 0


def test_sends_zpl_over_raw_tcp_when_printer_is_enabled(
    monkeypatch,
):
    fake_socket = FakePrinterSocket()
    captured_connection = {}

    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_HOST",
        "192.168.0.10",
    )
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_PORT",
        9100,
    )
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_TIMEOUT_SECONDS",
        5.0,
    )
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_ENCODING",
        "utf-8",
    )

    def fake_create_connection(address, timeout):
        captured_connection["address"] = address
        captured_connection["timeout"] = timeout

        return fake_socket

    monkeypatch.setattr(
        label_printer_service.socket,
        "create_connection",
        fake_create_connection,
    )

    result = (
        label_printer_service.send_zpl_to_label_printer(
            "^XA^FDTEST^FS^XZ"
        )
    )

    assert captured_connection["address"] == (
        "192.168.0.10",
        9100,
    )
    assert captured_connection["timeout"] == 5.0
    assert fake_socket.sent_payload == b"^XA^FDTEST^FS^XZ"
    assert result.sent is True
    assert result.skipped is False
    assert result.bytes_sent == len(fake_socket.sent_payload)


def test_raises_when_enabled_printer_host_is_missing(
    monkeypatch,
):
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_ENABLED",
        True,
    )
    monkeypatch.setattr(
        label_printer_service.settings,
        "LABEL_PRINTER_HOST",
        "",
    )

    with pytest.raises(
        label_printer_service.LabelPrinterError,
        match="LABEL_PRINTER_HOST",
    ):
        label_printer_service.send_zpl_to_label_printer(
            "^XA^XZ"
        )