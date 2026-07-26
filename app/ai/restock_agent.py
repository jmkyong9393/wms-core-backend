import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.schemas.restock import (
    RestockRecommendationRequest,
    RestockRecommendationResponse,
)

logger = logging.getLogger(__name__)

def restock_agent(
    request: RestockRecommendationRequest,
) -> RestockRecommendationResponse:
    """
    자동 발주 추천 Agent

    반려 도서의 최근 판매량, 현재 재고, 반려 수량을 바탕으로
    LLM이 추천 발주 수량과 발주 사유를 생성한다.

    - 입력: RestockRecommendationRequest
    - 출력: RestockRecommendationResponse
    - 응답 형식: JSON Schema 기반 Structured Output

    TODO:
    현재 입력·출력 스키마는 연동 테스트용 임시 규격이다.
    실제 반려 처리 API 및 order_proposals 규격 확정 후 수정한다.

    TODO:
    현재 최근 판매량을 한 번의 발주 주기에 필요한
    예상 수요로 간주한다.
    판매 집계 기간, 안전재고, 공급 리드타임이 확정되면
    프롬프트의 발주 판단 기준을 수정한다.
    """

    logger.info(
        "Restock Agent 실행"
    )   

    if not settings.OPENAI_API_KEY.strip():
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않아 "
            "Restock Agent를 호출할 수 없습니다."
        )

    llm = ChatOpenAI(
        model=settings.RESTOCK_AGENT_MODEL,
        temperature=settings.RESTOCK_AGENT_TEMPERATURE,
        api_key=settings.OPENAI_API_KEY,
    )

    # Pydantic 스키마 기반 JSON 응답을 강제한다.
    structured_llm = llm.with_structured_output(
        RestockRecommendationResponse,
        method="json_schema",
        strict=True,
    )

    messages = [
        SystemMessage(
            content=(
                "당신은 B2B 도서 물류센터의 자동 발주 추천 Agent입니다.\n\n"

                "[목표]\n"
                "도서 판매량, 현재 창고 가용 재고, 입고 반려 수량을 "
                "근거로 대체 발주 수량과 발주 사유를 작성합니다.\n\n"

                "[현재 임시 판단 기준]\n"
                "1. recent_sales_quantity는 한 번의 발주 주기에 필요한 "
                "예상 수요로 간주합니다.\n"
                "2. 현재 창고 재고는 예상 수요를 충족하는 데 사용할 수 있는 "
                "가용 재고입니다.\n"
                "3. 입고 반려 수량은 사용할 수 없으므로 대체가 필요한 "
                "수량으로 고려합니다.\n"
                "4. 추천 발주 수량은 예상 수요, 현재 재고, 반려 수량을 "
                "종합해 계산합니다.\n"
                "5. 계산 결과가 음수라면 추천 발주 수량은 0입니다.\n\n"

                "[응답 규칙]\n"
                "1. 입력으로 제공된 값만 사용하세요.\n"
                "2. 없는 안전재고, 리드타임, 공급처 정보는 추측하지 마세요.\n"
                "3. recommended_order_quantity는 0 이상의 정수로 작성하세요.\n"
                "4. reason_summary에는 추천 수량과 핵심 이유를 간결하게 "
                "설명하세요.\n"
                "5. evidence에는 판매량, 현재 재고, 반려 수량 및 계산 근거를 "
                "구체적으로 작성하세요.\n"
                "6. risk_level은 HIGH, MEDIUM, LOW 중 하나로 작성하세요.\n"
                "7. 반드시 지정된 JSON Schema 형식으로만 응답하세요."
            )
        ),
        HumanMessage(
            content=(
                "[도서 및 재고 데이터]\n"
                f"- ISBN: {request.isbn}\n"
                f"- 도서명: {request.book_title}\n"
                f"- 최근 판매량: {request.recent_sales_quantity}권\n"
                f"- 현재 창고 가용 재고: {request.current_stock}권\n"
                f"- 입고 반려 수량: {request.rejected_quantity}권\n"
                f"- 반려 사유 코드: "
                f"{request.rejection_reason_code}\n\n"

                "위 데이터를 근거로 최적의 대체 발주 수량을 계산하고, "
                "관리자가 이해할 수 있는 발주 사유와 판단 근거를 작성하세요."
            )
        ),
    ]

    result = structured_llm.invoke(messages)

    if not isinstance(
        result,
        RestockRecommendationResponse,
    ):
        raise TypeError(
            "Restock Agent가 예상된 응답 스키마를 "
            "반환하지 않았습니다."
        )

    print(
        "[Agent] Restock Agent 발주 추천 완료 "
        f"- 추천 수량: {result.recommended_order_quantity}"
    )

    return result