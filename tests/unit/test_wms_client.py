from app.services import wms_client


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "condition_grade": "EXCELLENT",
            "inventory_changed": True,
        }


def test_call_wms_inspection_result_api(monkeypatch):
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
        return FakeResponse()

    monkeypatch.setattr(wms_client.httpx, "post", fake_post)

    result = wms_client.call_wms_inspection_result_api(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="APPROVE",
        ubci_score=92.5,
        defects=[{"type": "COVER_SCRATCH"}],
        location_id="00000000-0000-4000-8000-000000000002",
        idempotency_key="return-job:test",
    )

    assert captured["url"].endswith(
        "/api/v1/internal/inventory/inspection-results"
    )
    assert captured["json"] == {
        "return_job_id": "00000000-0000-4000-8000-000000000001",
        "decision": "APPROVE",
        "ubci_score": 92.5,
        "defects": [{"type": "COVER_SCRATCH"}],
        "location_id": "00000000-0000-4000-8000-000000000002",
    }
    assert captured["headers"]["Idempotency-Key"] == "return-job:test"
    assert result["condition_grade"] == "EXCELLENT"
