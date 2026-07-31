from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.pricing import DynamicPricingContextResponse
from app.services.pricing_context_service import (
    PricingContextIncompleteError,
    PricingContextNotFoundError,
    get_dynamic_pricing_context,
)


router = APIRouter()


@router.get(
    "/{lpn_barcode}/context",
    response_model=DynamicPricingContextResponse,
    operation_id="getDynamicPricingContext",
    summary="LPN 동적 가격 책정 컨텍스트 조회",
    description=(
        "Rule 기반 동적 가격 Agent가 사용할 원천 데이터를 조회합니다. "
        "가격을 직접 계산하지 않고 도서 정가, 내부 카테고리, UBCI 점수와 "
        "확정 품질 등급을 반환합니다."
    ),
    responses={
        404: {"description": "등록된 LPN 단품 재고를 찾을 수 없음"},
        409: {"description": "정가 또는 UBCI 점수가 확정되지 않음"},
    },
)
def get_pricing_context(
    lpn_barcode: str = Path(
        min_length=1,
        description="가격 책정 대상 중고·반품 단품의 LPN 바코드",
        examples=["LPN-12345678123456781234567812345678"],
    ),
    session: Session = Depends(get_session),
) -> DynamicPricingContextResponse:
    try:
        context = get_dynamic_pricing_context(session, lpn_barcode)
    except PricingContextNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LPN inventory item not found",
        ) from exc
    except PricingContextIncompleteError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return DynamicPricingContextResponse(
        inventory_used_item_id=context.inventory_used_item_id,
        lpn_barcode=context.lpn_barcode,
        book_id=context.book_id,
        isbn=context.isbn,
        base_price=context.base_price,
        category=context.category,
        ubci_score=context.ubci_score,
        condition_grade=context.condition_grade,
    )
