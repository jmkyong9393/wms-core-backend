from datetime import datetime
from zoneinfo import ZoneInfo

from app.domains.lpn.lpn_service import (
    format_lpn_barcode,
)


def test_format_lpn_barcode_uses_newzed_date_and_sequence_rule():
    issued_at = datetime(
        2026,
        8,
        7,
        10,
        30,
        tzinfo=ZoneInfo("Asia/Seoul"),
    )

    assert (
        format_lpn_barcode(
            issued_at=issued_at,
            sequence_number=42,
        )
        == "LPN-NZ-260807-000042"
    )