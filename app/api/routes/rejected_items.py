from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.dependencies.auth import require_admin_or_master
from app.core.database import get_session
from app.models.wms import User
from app.schemas.rejected_inventory import RejectedItemsDiscardResponse
from app.services.rejected_item_service import discard_all_rejected_items


router = APIRouter()


@router.post(
    "/discard-all",
    response_model=RejectedItemsDiscardResponse,
    operation_id="discardAllRejectedItems",
    summary="C Zone 폐기 대기 도서 일괄 처리",
    description=(
        "관리자가 C Zone의 모든 REJECT_HOLD 도서를 폐기 완료 처리합니다. "
        "레코드는 삭제하지 않고 DISCARDED 상태와 처리 시각을 남깁니다."
    ),
)
def discard_rejected_items(
    session: Session = Depends(get_session),
    _: User = Depends(require_admin_or_master),
) -> RejectedItemsDiscardResponse:
    try:
        discarded_count, discarded_at = discard_all_rejected_items(session)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return RejectedItemsDiscardResponse(
        discarded_count=discarded_count,
        discarded_at=discarded_at,
    )

