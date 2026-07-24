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
    summary="자동 발주 추천 Agent 임시 호출",
)
def create_mock_restock_recommendation(
    request: RestockRecommendationRequest,
    current_admin: User = Depends(
        require_admin_or_master
    ),
) -> RestockRecommendationResponse:
    """
    자동 발주 추천 Agent 연결 흐름을 확인하기 위한 임시 API.

    TODO:
    실제 반려 처리 API와 재고·판매량 조회 기능이 완성되면
    이 Mock API 대신 해당 처리 흐름에서
    generate_restock_recommendation()을 호출한다.

    TODO:
    order_proposals 저장 규격이 확정되면
    Agent 결과 저장 로직을 별도 서비스에서 연결한다.
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