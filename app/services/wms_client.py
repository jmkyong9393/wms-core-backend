import os
from typing import Any

import httpx


WMS_BASE_URL = os.getenv(
    "WMS_BASE_URL",
    "http://api:8000",
).rstrip("/")

# WMS API 요청 제한 시간
WMS_REQUEST_TIMEOUT_SECONDS = 10.0

WMS_INSPECTION_RESULT_PATH = "/api/v1/internal/inventory/inspection-results"

def post_wms_request(
    path: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    response = httpx.post(
        f"{WMS_BASE_URL}{path}",
        json=payload,
        headers={
            "Idempotency-Key": idempotency_key,
        },
        timeout=WMS_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    return response.json()


def call_wms_inspection_result_api(
    return_job_id: str,
    decision: str,
    ubci_score: int | float | None,
    defects: list[dict[str, Any]],
    location_id: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    return post_wms_request(
        path=WMS_INSPECTION_RESULT_PATH,
        payload={
            "return_job_id": return_job_id,
            "decision": decision,
            "ubci_score": ubci_score,
            "defects": defects,
            "location_id": location_id,
        },
        idempotency_key=idempotency_key,
    )
