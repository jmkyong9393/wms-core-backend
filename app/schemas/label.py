from enum import Enum


class LabelPrintStatus(str, Enum):
    """라벨 프린터 전송 결과 상태다."""

    SENT = "SENT"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"