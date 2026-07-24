from app.ai.restock_agent import restock_agent
from app.schemas.restock import (
    RestockRecommendationRequest,
    RestockRecommendationResponse,
)


def generate_restock_recommendation(
    request: RestockRecommendationRequest,
) -> RestockRecommendationResponse:
    """
    수집된 반려·판매·재고 데이터를 Restock Agent에 전달한다.

    TODO:
    현재 request는 연동 테스트용 임시 입력 스키마다.
    실제 반려 처리 API와 재고·판매량 조회 기능이 완성되면
    해당 결과를 RestockRecommendationRequest로 변환해 전달한다.

    TODO:
    Agent 결과를 order_proposals 테이블에 저장하는 로직은
    DB 모델과 저장 규격이 확정된 뒤 연결한다.
    """
    return restock_agent(request)