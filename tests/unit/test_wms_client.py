from app.services import wms_client


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        json_data=None,
        content=b'{"result": "ok"}',
        text='{"result": "ok"}',
    ):
        self.status_code = status_code
        self.json_data = json_data or {}
        self.content = content
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self.json_data


def test_call_wms_inspection_result_api_sends_hitl_fields(
    monkeypatch,
):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )

        return FakeResponse(
            status_code=200,
            json_data={
                "condition_grade": "NORMAL",
                "putaway_changed": True,
            },
        )

    monkeypatch.setattr(
        wms_client.httpx,
        "post",
        fake_post,
    )

    result = wms_client.call_wms_inspection_result_api(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="APPROVE",
        ubci_score=72.5,
        defects=[{"type": "COVER_SCRATCH"}],
        idempotency_key="return-job:test-job-id",
        admin_decision_code="APPROVE_DOWNGRADE",
        final_grade="NORMAL",
        rejection_disposition=None,
    )

    assert captured["url"].endswith(
        "/api/v1/internal/inventory/inspection-results"
    )

    assert captured["json"] == {
        "return_job_id": (
            "00000000-0000-4000-8000-000000000001"
        ),
        "decision": "APPROVE",
        "ubci_score": 72.5,
        "defects": [{"type": "COVER_SCRATCH"}],
        "admin_decision_code": "APPROVE_DOWNGRADE",
        "final_grade": "NORMAL",
        "rejection_disposition": None,
    }

    assert captured["headers"]["Idempotency-Key"] == (
        "return-job:test-job-id"
    )
    assert result["condition_grade"] == "NORMAL"
    assert result["putaway_changed"] is True
