from uuid import uuid4

from app.models.wms import ConditionGrade, PutawayStatus
from app.schemas.lpn import LpnLocationDetail
from app.schemas.putaway import PutawayConfirmationResponse


def test_putaway_confirmation_exposes_inventory_result():
    response = PutawayConfirmationResponse(
        lpn_barcode="LPN-TEST-CONFIRM",
        inbound_item_id=uuid4(),
        putaway_job_id=uuid4(),
        putaway_status=PutawayStatus.COMPLETED,
        condition_grade=ConditionGrade.NEW,
        location=LpnLocationDetail(
            id=uuid4(),
            barcode="A-3-1",
            zone="A",
            rack="3",
            shelf="1",
        ),
        inventory_kind="NEW_AGGREGATE",
        inventory_id=uuid4(),
        stock_changed=True,
    )

    assert response.putaway_status == PutawayStatus.COMPLETED
    assert response.inventory_kind == "NEW_AGGREGATE"
    assert response.stock_changed is True
