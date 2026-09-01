import os
from typing import Any

import httpx
from fastapi import status

from app.core.config import settings


class WMSRetryableError(Exception):
    """일시적인 WMS 장애로 재시도가 가능한 오류."""


class WMSNonRetryableError(Exception):
    """요청 수정 없이는 해결되지 않아 재시도하지 않는 오류."""


WMS_BASE_URL = os.getenv(
    "WMS_BASE_URL",
    "http://api:8000",
).rstrip("/")


WMS_INSPECTION_RESULT_PATH = "/api/v1/internal/inventory/inspection-results"


def post_wms_request(
    path: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    try:
        response = httpx.post(
            f"{WMS_BASE_URL}{path}",
            json=payload,
            headers={
                "Idempotency-Key": idempotency_key,
            },
            timeout=settings.WMS_REQUEST_TIMEOUT_SECONDS,
        )

    # 타임아웃, 연결 실패, DNS 오류 등
    except httpx.RequestError as error:
        raise WMSRetryableError(f"WMS 통신에 실패했습니다: {error}") from error

    status_code = response.status_code

    # 일시적 장애로 판단하여 재시도
    if status_code in {408, 429} or 500 <= status_code < 600:
        raise WMSRetryableError(f"WMS 일시적 오류가 발생했습니다. status_code={status_code} response={response.text}")

    # 요청 형식, 인증, 존재하지 않는 API 등
    if 400 <= status_code < 500:
        raise WMSNonRetryableError(f"WMS 요청을 처리할 수 없습니다. status_code={status_code} response={response.text}")

    response.raise_for_status()

    if response.status_code == status.HTTP_204_NO_CONTENT:
        return {
            "status_code": response.status_code,
            "message": "WMS 요청이 정상 처리되었습니다.",
        }

    if not response.content:
        return {
            "status_code": response.status_code,
            "message": "WMS 요청이 정상 처리되었습니다.",
        }

    try:
        return response.json()
    except ValueError:
        return {
            "status_code": response.status_code,
            "raw_response": response.text,
        }


def call_wms_inspection_result_api(
    return_job_id: str,
    decision: str,
    ubci_score: int | float | None,
    defects: list[dict[str, Any]],
    idempotency_key: str,
    admin_decision_code: str | None = None,
    final_grade: str | None = None,
    rejection_disposition: str | None = None,
) -> dict[str, Any]:
    return post_wms_request(
        path=WMS_INSPECTION_RESULT_PATH,
        payload={
            "return_job_id": return_job_id,
            "decision": decision,
            "ubci_score": ubci_score,
            "defects": defects,
            "admin_decision_code": admin_decision_code,
            "final_grade": final_grade,
            "rejection_disposition": rejection_disposition,
        },
        idempotency_key=idempotency_key,
    )
