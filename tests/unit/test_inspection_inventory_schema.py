from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domains.inventory.schemas.inspection_inventory import InspectionInventoryRequest


def test_approve_requires_score_or_final_grade():
    with pytest.raises(ValidationError):
        InspectionInventoryRequest(
            return_job_id="00000000-0000-4000-8000-000000000001",
            decision="APPROVE",
        )


def test_reject_does_not_receive_location():
    request = InspectionInventoryRequest(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="REJECT",
        ubci_score="62.75",
    )

    assert "location_id" not in InspectionInventoryRequest.model_fields
    assert request.ubci_score == Decimal("62.75")


def test_approve_accepts_score_without_location():
    request = InspectionInventoryRequest(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="APPROVE",
        ubci_score="95.25",
    )

    assert request.ubci_score == Decimal("95.25")


@pytest.mark.parametrize("score", ["-0.01", "100.01"])
def test_score_must_be_in_ubci_range(score):
    with pytest.raises(ValidationError):
        InspectionInventoryRequest(
            return_job_id="00000000-0000-4000-8000-000000000001",
            decision="REJECT",
            ubci_score=score,
        )


def test_reject_allows_missing_score():
    request = InspectionInventoryRequest(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="REJECT",
    )

    assert request.ubci_score is None


def test_accepts_hitl_contract_fields():
    request = InspectionInventoryRequest(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="REJECT",
        admin_decision_code="DMG_EXT_WET",
        final_grade="REJECT",
        rejection_disposition="REJECT_RETURN",
    )

    assert request.admin_decision_code.value == "DMG_EXT_WET"
    assert request.final_grade.value == "REJECT"
    assert request.rejection_disposition == "REJECT_RETURN"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admin_decision_code", "APPROVE_DOWNGRADE"),
        ("admin_decision_code", "DMG_EXT_WATER"),
        ("final_grade", "GOOD"),
        ("rejection_disposition", "RETURN"),
        ("rejection_disposition", "DISCARD"),
    ],
)
def test_rejects_invalid_hitl_contract_values(field, value):
    payload = {
        "return_job_id": ("00000000-0000-4000-8000-000000000001"),
        "decision": "REJECT",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        InspectionInventoryRequest(**payload)


def test_approve_accepts_admin_final_grade_without_ubci_score():
    request = InspectionInventoryRequest(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="APPROVE",
        admin_decision_code="FP_SHADOW",
        final_grade="NORMAL",
    )

    assert request.ubci_score is None
    assert request.final_grade.value == "NORMAL"
