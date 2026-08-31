from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.domains.inventory import inspection_inventory
from app.models.wms import ConditionGrade
from app.domains.inventory.schemas.inspection_inventory import (
    InspectionInventoryRequest,
)
from app.domains.inventory.used_inventory_service import InspectionAdmissionResult


def _request(decision: str = "APPROVE") -> InspectionInventoryRequest:
    return InspectionInventoryRequest(
        return_job_id=uuid4(),
        decision=decision,
        ubci_score="91.50" if decision == "APPROVE" else None,
        defects=[],
        rejection_disposition=(
            None
            if decision == "APPROVE"
            else "REJECT_DISCARD"
        ),
    )


def _admission_result(
    *,
    decision: str = "APPROVE",
    inventory_changed: bool = True,
) -> InspectionAdmissionResult:
    return InspectionAdmissionResult(
        return_job_id=uuid4(),
        inbound_item_id=uuid4(),
        decision=decision,
        condition_grade=(
            ConditionGrade.EXCELLENT
            if decision == "APPROVE"
            else ConditionGrade.REJECT
        ),
        lpn_barcode="LPN-PRICING-001",
        location_id=uuid4(),
        location_barcode="B-3-1" if decision == "APPROVE" else "C-3-1",
        inventory_used_item_id=(uuid4() if decision == "APPROVE" else None),
        rejected_item_id=(uuid4() if decision == "REJECT" else None),
        inventory_changed=inventory_changed,
    )


def test_runs_pricing_after_new_used_inventory_is_committed(monkeypatch):
    result = _admission_result()
    session = Mock()
    apply_result = Mock(return_value=result)
    execute_pricing = Mock()
    monkeypatch.setattr(
        inspection_inventory,
        "apply_inspected_item_result",
        apply_result,
    )
    monkeypatch.setattr(
        inspection_inventory,
        "execute_dynamic_pricing",
        execute_pricing,
    )
    monkeypatch.setattr(
        inspection_inventory,
        "_try_print_ubci_label",
        Mock(return_value=("SKIPPED", None)),
    )
    session.get.side_effect = [Mock(), Mock(), Mock()]

    inspection_inventory.apply_inspection_inventory_result(
        request=_request(),
        session=session,
    )

    execute_pricing.assert_called_once_with(
        session=session,
        lpn_barcode="LPN-PRICING-001",
    )
    assert session.commit.call_count == 2


@pytest.mark.parametrize(
    ("decision", "inventory_changed"),
    [("REJECT", True), ("APPROVE", False)],
)
def test_skips_pricing_without_new_sellable_inventory(
    monkeypatch,
    decision,
    inventory_changed,
):
    result = _admission_result(
        decision=decision,
        inventory_changed=inventory_changed,
    )
    session = Mock()
    monkeypatch.setattr(
        inspection_inventory,
        "apply_inspected_item_result",
        Mock(return_value=result),
    )
    execute_pricing = Mock()
    monkeypatch.setattr(
        inspection_inventory,
        "execute_dynamic_pricing",
        execute_pricing,
    )

    inspection_inventory.apply_inspection_inventory_result(
        request=_request(decision),
        session=session,
    )

    execute_pricing.assert_not_called()
    session.commit.assert_called_once()


def test_keeps_admitted_inventory_when_dynamic_pricing_fails(monkeypatch):
    result = _admission_result()
    session = Mock()
    monkeypatch.setattr(
        inspection_inventory,
        "apply_inspected_item_result",
        Mock(return_value=result),
    )
    monkeypatch.setattr(
        inspection_inventory,
        "execute_dynamic_pricing",
        Mock(side_effect=RuntimeError("Pricing Agent unavailable")),
    )
    monkeypatch.setattr(
        inspection_inventory,
        "_try_print_ubci_label",
        Mock(return_value=("SKIPPED", None)),
    )
    session.get.side_effect = [Mock(), Mock(), Mock()]

    response = inspection_inventory.apply_inspection_inventory_result(
        request=_request(),
        session=session,
    )

    assert response.inventory_used_item_id == result.inventory_used_item_id
    assert response.inventory_changed is True
    session.commit.assert_called_once()
    session.rollback.assert_called_once()
