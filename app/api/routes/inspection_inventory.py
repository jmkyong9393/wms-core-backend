from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.inspection_inventory import (
    InspectionInventoryRequest,
    InspectionInventoryResponse,
)
from app.services.used_inventory_service import admit_inspected_item


router = APIRouter()


@router.post(
    "/inspection-results",
    response_model=InspectionInventoryResponse,
    operation_id="applyInspectionInventoryResult",
    summary="검수 결과 기반 LPN 재고 편입",
    description=(
        "AI 검수 결과를 입고 품목에 적용합니다. 승인 결과는 UBCI 정책으로 "
        "등급을 계산한 뒤 중고 단품 재고와 재고 원장을 생성하고, 반려 "
        "결과는 판매 가능 재고를 생성하지 않습니다. 같은 검수 결과가 "
        "재전송되어도 재고는 한 번만 반영됩니다."
    ),
    responses={
        404: {"description": "검수 작업 또는 승인 로케이션을 찾을 수 없음"},
        409: {"description": "검수·입고 상태 또는 재전송 결정이 충돌함"},
    },
)
def apply_inspection_inventory_result(
    request: InspectionInventoryRequest,
    session: Session = Depends(get_session),
) -> InspectionInventoryResponse:
    try:
        result = admit_inspected_item(
            session=session,
            return_job_id=request.return_job_id,
            decision=request.decision,
            ubci_score=request.ubci_score,
            defects=request.defects,
            location_barcode=request.location_barcode,
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
        inventory_used_item_id=result.inventory_used_item_id,
        inventory_status=result.inventory_status,
        inventory_changed=result.inventory_changed,
    )
