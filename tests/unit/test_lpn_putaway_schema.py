from uuid import uuid4

from app.models.wms import (
    BookCategory,
    ConditionGrade,
    InboundType,
    PutawayStatus,
)
from app.schemas.lpn import (
    LpnLocationDetail,
    LpnPutawayBookDetail,
    LpnPutawayResponse,
)


def test_lpn_putaway_response_exposes_final_storage_instruction():
    response = LpnPutawayResponse(
        lpn_barcode="LPN-TEST-PUTAWAY",
        inbound_item_id=uuid4(),
        inbound_type=InboundType.USED_PURCHASE,
        book=LpnPutawayBookDetail(
            id=uuid4(),
            isbn="9781234567890",
            title="중고 소설",
            category=BookCategory.NOVEL,
        ),
        condition_grade=ConditionGrade.EXCELLENT,
        putaway_job_id=uuid4(),
        putaway_status=PutawayStatus.WAITING,
        location=LpnLocationDetail(
            id=uuid4(),
            barcode="B-3-1",
            zone="B",
            rack="3",
            shelf="1",
        ),
    )

    assert response.putaway_status == PutawayStatus.WAITING
    assert response.location.barcode == "B-3-1"
    assert response.book.category == BookCategory.NOVEL
