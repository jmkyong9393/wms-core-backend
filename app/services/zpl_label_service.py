from decimal import Decimal

from app.core.config import settings
from app.services.lpn_service import build_label_scan_qr_url


def _mm_to_dots(length_mm: int) -> int:
    """
    mm 단위를 프린터 해상도 기준 dot 단위로 변환한다.

    203 DPI 기준으로 50mm × 30mm는 약 400 × 240 dots다.
    """
    return round(
        length_mm * settings.LABEL_PRINTER_DPI / 25.4
    )


def _sanitize_zpl_text(value: str) -> str:
    """DB 텍스트가 ZPL 제어문자로 해석되지 않도록 정리한다."""
    return (
        value.replace("^", " ")
        .replace("~", " ")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )


def _build_label_header() -> list[str]:
    """50mm × 30mm 라벨에 공통으로 적용할 ZPL 헤더다."""
    label_width = _mm_to_dots(
        settings.LABEL_PRINTER_LABEL_WIDTH_MM
    )
    label_height = _mm_to_dots(
        settings.LABEL_PRINTER_LABEL_HEIGHT_MM
    )

    return [
        "^XA",
        # 실제 프린터의 한글 설정에 따라 추후 조정할 수 있다.
        "^CI28",
        f"^PW{label_width}",
        f"^LL{label_height}",
        "^LH0,0",
    ]


def _build_scan_qr_zpl(
    certificate_token: str,
) -> str:
    """
    작업자·소비자가 공통으로 스캔할 QR ZPL을 생성한다.

    QR에는 LPN 원문이 아니라 프론트의 /scan/{token} URL을 넣는다.
    """
    scan_url = build_label_scan_qr_url(certificate_token)

    return f"^FO15,42^BQN,2,5^FDLA,{scan_url}^FS"


def build_lpn_label_zpl(
    *,
    lpn_barcode: str,
    certificate_token: str,
) -> str:
    """
    입고 접수 직후 책에 부착할 초기 LPN QR 라벨 ZPL을 생성한다.

    검수 전 라벨이므로 등급·UBCI 점수는 출력하지 않는다.
    동적 한글 도서명은 실제 프린터 폰트 매핑이 확정된 뒤 추가한다.
    """
    safe_lpn = _sanitize_zpl_text(lpn_barcode)

    commands = [
        *_build_label_header(),
        "^FO15,12^A0N,25,25^FDWMS LPN LABEL^FS",
        _build_scan_qr_zpl(certificate_token),
        f"^FO195,65^A0N,20,20^FD{safe_lpn}^FS",
        "^FO195,105^A0N,18,18^FDPENDING INSPECTION^FS",
        "^FO195,145^A0N,16,16^FDSCAN WITH WMS APP^FS",
        "^FO15,215^A0N,14,14^FDSCAN FOR ITEM OR CERTIFICATE^FS",
        "^XZ",
    ]

    return "\n".join(commands)


def build_ubci_label_zpl(
    *,
    lpn_barcode: str,
    certificate_token: str,
    condition_grade: str,
    ubci_score: Decimal | None,
) -> str:
    """
    검수 확정 후 사용할 UBCI QR 라벨 ZPL을 생성한다.

    초기 LPN 라벨과 같은 QR을 사용하고, 확정 등급·UBCI 점수를 출력한다.
    """
    safe_lpn = _sanitize_zpl_text(lpn_barcode)
    safe_grade = _sanitize_zpl_text(condition_grade)
    score_text = (
        f"{ubci_score:.2f}"
        if ubci_score is not None
        else "-"
    )

    commands = [
        *_build_label_header(),
        "^FO15,12^A0N,25,25^FDWMS UBCI CERTIFICATE^FS",
        _build_scan_qr_zpl(certificate_token),
        f"^FO195,58^A0N,20,20^FD{safe_lpn}^FS",
        f"^FO195,95^A0N,20,20^FDGRADE: {safe_grade}^FS",
        f"^FO195,130^A0N,20,20^FDUBCI: {score_text}^FS",
        "^FO195,165^A0N,16,16^FDSCAN FOR CERTIFICATE^FS",
        "^FO15,215^A0N,14,14^FDSCAN FOR ITEM OR CERTIFICATE^FS",
        "^XZ",
    ]

    return "\n".join(commands)


def build_custom_label_zpl(
    *,
    lpn_barcode: str,
    title: str,
    isbn: str,
    worker_id: str,
) -> str:
    """
    프론트엔드 모달에서 입력된 정보로 라벨 ZPL을 생성한다.
    """
    safe_lpn = _sanitize_zpl_text(lpn_barcode)
    safe_title = _sanitize_zpl_text(title)
    safe_isbn = _sanitize_zpl_text(isbn)
    safe_worker = _sanitize_zpl_text(worker_id)
    
    # QR URL (단순하게 LPN 바코드를 넣거나, 프론트엔드 스캔 URL로 설정)
    from app.core.config import settings
    scan_url = f"{settings.PUBLIC_WEB_BASE_URL}/scan/{safe_lpn}"
    
    commands = [
        *_build_label_header(),
        f"^FO15,12^A0N,20,20^FD{safe_title}^FS",
        f"^FO15,35^A0N,18,18^FDISBN: {safe_isbn}^FS",
        f"^FO15,60^BQN,2,4^FDLA,{scan_url}^FS",
        f"^FO100,70^A0N,20,20^FDLPN: {safe_lpn}^FS",
        f"^FO100,100^A0N,18,18^FDWorker: {safe_worker}^FS",
        "^XZ",
    ]
    return "\n".join(commands)