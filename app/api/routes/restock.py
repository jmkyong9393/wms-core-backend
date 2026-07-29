from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from app.api.dependencies.auth import require_admin_or_master
from app.models.wms import User
from app.schemas.restock import (
    RestockRecommendationRequest,
    RestockRecommendationResponse,
)
from app.services.restock_service import (
    generate_restock_recommendation,
)


router = APIRouter()


@router.post(
    "/mock-recommendation",
    response_model=RestockRecommendationResponse,
    summary="자동 발주 추천 Agent 수동 호출(개발 확인용)",
)
def create_mock_restock_recommendation(
    request: RestockRecommendationRequest,
    current_admin: User = Depends(
        require_admin_or_master
    ),
) -> RestockRecommendationResponse:
    """
    Restock Agent를 수동으로 호출해 추천 결과만 확인하는 개발용 API.

    실제 업무 흐름에서는 최종 반려된 검수 작업을 기준으로
    Worker가 Restock 추천안을 생성하고 OrderProposal에 저장한다.

    이 API는 OrderProposal 저장, 관리자 알림, AUTO_PO 생성은 수행하지 않는다.
    """
    try:
        return generate_restock_recommendation(
            request
        )

    except RuntimeError as exc:
        # API Key 미설정 등 현재 서버 설정 문제
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        # OpenAI API 통신, 응답 변환, Structured Output 처리 오류
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "자동 발주 추천 Agent 처리 중 오류가 발생했습니다."
            ),
        ) from exc