from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session

from app.core.database import get_session
from app.domains.pricing.dynamic_pricing_service import execute_dynamic_pricing
from app.domains.pricing.schemas.pricing import (
    DynamicPricingContextResponse,
    DynamicPricingResultRequest,
    DynamicPricingResultResponse,
)
from app.domains.pricing.pricing_context_service import (
    PricingContextIncompleteError,
    PricingContextNotFoundError,
    apply_dynamic_pricing_result,
    get_dynamic_pricing_context,
)


router = APIRouter()


def _build_result_response(result) -> DynamicPricingResultResponse:
    return DynamicPricingResultResponse(
        inventory_used_item_id=result.inventory_used_item_id,
        lpn_barcode=result.lpn_barcode,
        base_price=result.base_price,
        discount_rate=result.discount_rate,
        sale_price=result.sale_price,
        pricing_changed=result.pricing_changed,
    )


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


@router.post(
    "/results",
    response_model=DynamicPricingResultResponse,
    operation_id="applyDynamicPricingResult",
    summary="LPN 동적 가격 산정 결과 저장",
    description=(
        "Rule 기반 동적 가격 Agent가 산정한 할인율과 최종 판매가격을 "
        "중고·반품 LPN 단품 재고에 저장합니다. 동일 LPN에 대한 재산정 "
        "결과는 기존 값을 갱신합니다."
    ),
    responses={
        404: {"description": "등록된 LPN 단품 재고를 찾을 수 없음"},
        409: {
            "description": "정가·UBCI 미확정 또는 가격 정책 범위를 벗어난 결과"
        },
    },
)
def save_pricing_result(
    request: DynamicPricingResultRequest,
    session: Session = Depends(get_session),
) -> DynamicPricingResultResponse:
    try:
        result = apply_dynamic_pricing_result(
            session,
            lpn_barcode=request.lpn_barcode,
            discount_rate=request.discount_rate,
            final_price=request.final_price,
        )
        session.commit()
    except PricingContextNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LPN inventory item not found",
        ) from exc
    except PricingContextIncompleteError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        session.rollback()
        raise

    return _build_result_response(result)


@router.post(
    "/{lpn_barcode}/recalculate",
    response_model=DynamicPricingResultResponse,
    operation_id="recalculateDynamicPricing",
    summary="LPN 동적 가격 재산정",
    description=(
        "가격 산정에 실패했거나 재산정이 필요한 판매 가능 LPN의 DB 컨텍스트를 "
        "이용해 Pricing Agent를 동기 실행하고 할인율과 판매가격을 갱신합니다."
    ),
    responses={
        404: {"description": "등록된 LPN 단품 재고를 찾을 수 없음"},
        409: {
            "description": "판매 불가능 상태이거나 정가·UBCI가 확정되지 않음"
        },
    },
)
def recalculate_dynamic_pricing(
    lpn_barcode: str = Path(
        min_length=1,
        description="가격을 다시 산정할 중고·반품 단품의 LPN 바코드",
        examples=["LPN-12345678123456781234567812345678"],
    ),
    session: Session = Depends(get_session),
) -> DynamicPricingResultResponse:
    try:
        result = execute_dynamic_pricing(
            session=session,
            lpn_barcode=lpn_barcode,
        )
        session.commit()
    except PricingContextNotFoundError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="LPN inventory item not found",
        ) from exc
    except PricingContextIncompleteError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except Exception:
        session.rollback()
        raise

    return _build_result_response(result)
