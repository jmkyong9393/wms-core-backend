from enum import Enum

from pydantic import BaseModel, Field


class LabelPrintStatus(str, Enum):
    """라벨 프린터 전송 결과 상태값."""

    SENT = "SENT"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class LabelType(str, Enum):
    """재출력할 물리 라벨의 업무 유형."""

    LPN = "LPN"
    UBCI = "UBCI"


class LabelReprintResponse(BaseModel):
    """
    작업자가 요청한 LPN 또는 UBCI 라벨 재출력 결과.

    재출력은 라벨 전송만 수행하며 입고·검수·재고 데이터를 변경하지 않는다.
    """

    lpn_barcode: str = Field(
        description="재출력을 요청한 물리 단품 LPN",
    )
    label_type: LabelType = Field(
        description="재출력한 라벨 유형",
    )
    label_print_status: LabelPrintStatus = Field(
        description=(
            "SENT=프린터 전송 완료, "
            "SKIPPED=프린터 비활성화, "
            "FAILED=전송 실패"
        ),
    )
    label_print_error: str | None = Field(
        default=None,
        description=(
            "재출력 실패 시 작업자 안내 메시지. "
            "프린터 IP 등 내부 설정은 포함하지 않는다."
        ),
    )