import os
from typing import Any, Dict

import httpx


WMS_BASE_URL = os.getenv(
    "WMS_BASE_URL",
    "http://api:8000",
)

# WMS API 요청 제한 시간
WMS_REQUEST_TIMEOUT = 10.0


# 정상 판정된 도서를 WMS 입고 처리
def call_wms_approve_api(
    book_id: str,
) -> Dict[str, Any]:
    # 실제 endpoint와 Body는
    # WMS 담당자 스펙 확정 후 수정 필요
    response = httpx.post(
        f"{WMS_BASE_URL}/api/inventory/approve",
        json={
            "book_id": book_id,
            "reason": "AI_INSPECTION_PASSED",
        },
        timeout=WMS_REQUEST_TIMEOUT,
    )

    # 4xx 또는 5xx 응답이면 HTTPStatusError 발생
    response.raise_for_status()

    return response.json()


# 불량 판정된 도서를 WMS 반려 처리
def call_wms_reject_api(
    book_id: str,
    reason: str,
) -> Dict[str, Any]:
    # 실제 endpoint와 Body는
    # WMS 담당자 스펙 확정 후 수정 필요
    response = httpx.post(
        f"{WMS_BASE_URL}/api/inventory/reject",
        json={
            "book_id": book_id,
            "reason": reason,
        },
        timeout=WMS_REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()