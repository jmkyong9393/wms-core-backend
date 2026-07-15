import os
from typing import Any

import httpx


WMS_BASE_URL = os.getenv(
    "WMS_BASE_URL",
    "http://api:8000",
).rstrip("/")

# WMS API 요청 제한 시간
WMS_REQUEST_TIMEOUT_SECONDS = 10.0

# 실제 WMS API 스펙 확정 후 경로와 요청 Body 조정 필요
WMS_APPROVE_PATH = "/api/inventory/approve"
WMS_REJECT_PATH = "/api/inventory/reject"

def post_wms_request(
        path: str,
        payload: dict[str,Any],
) -> dict[str, Any]:
    response = httpx.post(
        f"{WMS_BASE_URL}{path}",
        json=payload,
        timeout=WMS_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    return response.json()


# 정상 판정된 도서를 WMS 입고 처리
def call_wms_approve_api(
    book_id: str,
) -> dict[str, Any]:
    return post_wms_request(
        path=WMS_APPROVE_PATH,
        payload={
            "book_id": book_id,
            "reason": "AI_INSPECTION_PASSED",
        },
    )


# 불량 판정된 도서를 WMS 반려 처리
def call_wms_reject_api(
    book_id: str,
    reason: str,
) -> dict[str, Any]:
    return post_wms_request(
        path=WMS_REJECT_PATH,
        payload={
            "book_id": book_id,
            "reason": reason,
        },
    )