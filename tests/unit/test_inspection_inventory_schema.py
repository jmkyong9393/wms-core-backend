from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.inspection_inventory import InspectionInventoryRequest


def test_approve_requires_location_barcode():
    with pytest.raises(ValidationError):
        InspectionInventoryRequest(
            return_job_id="00000000-0000-4000-8000-000000000001",
            decision="APPROVE",
            ubci_score="92.50",
        )


def test_reject_does_not_require_location_barcode():
    request = InspectionInventoryRequest(
        return_job_id="00000000-0000-4000-8000-000000000001",
        decision="REJECT",
        ubci_score="62.75",
    )

    assert request.location_barcode is None
    assert request.ubci_score == Decimal("62.75")


@pytest.mark.parametrize("score", ["-0.01", "100.01"])
def test_score_must_be_in_ubci_range(score):
    with pytest.raises(ValidationError):
        InspectionInventoryRequest(
            return_job_id="00000000-0000-4000-8000-000000000001",
            decision="REJECT",
            ubci_score=score,
        )
