from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.inspection_inventory import (
    InspectionInventoryRequest,
    InspectionInventoryResponse,
)
from app.services.used_inventory_service import assign_inspected_item_putaway


router = APIRouter()


@router.post(
    "/inspection-results",
    response_model=InspectionInventoryResponse,
    operation_id="applyInspectionInventoryResult",
    summary="검수 결과 기반 LPN 적재 로케이션 확정",
    description=(
        "AI 검수 결과를 입고 품목에 적용합니다. 승인 결과는 UBCI 정책으로 "
        "등급을 계산하여 B Zone 로케이션을 확정하고, 반려 결과는 C Zone "
        "로케이션을 확정합니다. 작업자가 실제 적재를 완료하기 전에는 "
        "판매 가능 재고를 생성하지 않습니다."
    ),
    responses={
        404: {"description": "검수 작업을 찾을 수 없음"},
        409: {"description": "검수·입고 상태 충돌 또는 가용 로케이션 없음"},
    },
)
def apply_inspection_inventory_result(
    request: InspectionInventoryRequest,
    session: Session = Depends(get_session),
) -> InspectionInventoryResponse:
    try:
        result = assign_inspected_item_putaway(
            session=session,
            return_job_id=request.return_job_id,
            decision=request.decision,
            ubci_score=request.ubci_score,
            defects=request.defects,
            admin_decision_code=request.admin_decision_code,
            final_grade=request.final_grade,
            rejection_disposition=request.rejection_disposition,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return InspectionInventoryResponse(
        return_job_id=result.return_job_id,
        inbound_item_id=result.inbound_item_id,
        decision=result.decision,
        condition_grade=result.condition_grade,
        lpn_barcode=result.lpn_barcode,
        putaway_job_id=result.putaway_job_id,
        putaway_status=result.putaway_status,
        location_id=result.location_id,
        location_barcode=result.location_barcode,
        putaway_changed=result.putaway_changed,
    )
