import logging
import os

from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI

from app.schemas.pricing import (
    PricingReason,
    PricingRecommendationRequest,
    PricingRecommendationResponse,
)


load_dotenv()

logger = logging.getLogger(__name__)


def round_to_hundred(
    price: Decimal,
) -> int:
    """가격의 100원 단위 반올림."""

    rounded = (
        price / Decimal("100")
    ).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )

    return int(
        rounded * Decimal("100")
    )


def pricing_agent(
    request: PricingRecommendationRequest,
) -> PricingRecommendationResponse:
    """최종 책정가와 AI 가격 산정 사유 생성."""

    api_key = os.getenv(
        "OPENAI_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않아 "
            "Pricing Agent를 호출할 수 없습니다."
        )

    # 백엔드 기초 가격의 100원 단위 확정
    final_price = round_to_hundred(
        request.base_calculated_price
    )

    llm = ChatOpenAI(
        model=os.getenv(
            "OPENAI_MODEL",
            "gpt-4o-mini",
        ),
        temperature=0,
        api_key=api_key,
    )

    # 가격 사유 JSON 응답 강제
    structured_llm = (
        llm.with_structured_output(
            PricingReason,
            method="json_schema",
            strict=True,
        )
    )

    messages = [
        SystemMessage(
            content=(
                "당신은 WMS 중고 도서 가격 책정 "
                "Pricing Agent입니다.\n\n"
                "[역할]\n"
                "백엔드가 계산한 기초 책정가와 "
                "확정된 최종 가격을 바탕으로 "
                "가격 책정 사유를 작성합니다.\n\n"
                "[금지 사항]\n"
                "1. 새로운 시장 데이터나 수요 정보를 "
                "추측하지 마세요.\n"
                "2. 입력으로 받은 UBCI 점수와 등급을 "
                "변경하지 마세요.\n"
                "3. 확정된 최종 가격을 변경하지 마세요.\n"
                "4. 할인율이나 정책을 임의로 만들지 마세요.\n\n"
                "[응답 규칙]\n"
                "1. 도서 카테고리, UBCI 점수, 품질 등급, "
                "가상 수요만 근거로 사용하세요.\n"
                "2. 소비자와 관리자가 이해할 수 있는 "
                "한국어 2~3문장으로 작성하세요.\n"
                "3. 반드시 지정된 JSON Schema 형식으로만 "
                "응답하세요."
            )
        ),
        HumanMessage(
            content=(
                "[도서 가격 데이터]\n"
                f"- 도서명: {request.book_title}\n"
                f"- 카테고리: {request.category}\n"
                f"- 정가: {request.original_price}원\n"
                f"- UBCI 점수: {request.ubci_score}\n"
                f"- 품질 등급: {request.condition_grade}\n"
                f"- 기초 책정가: "
                f"{request.base_calculated_price}원\n"
                f"- 가상 수요: {request.virtual_demand}\n"
                f"- 확정 최종 가격: {final_price}원\n\n"
                "위 입력값만 사용하여 가격 숫자나 할인율 없이 "
                "가격 책정 근거를 1~2문장으로 작성하세요."
            )
        ),
    ]

    result = structured_llm.invoke(
        messages
    )

    if not isinstance(
        result,
        PricingReason,
    ):
        raise TypeError(
            "Pricing Agent가 예상된 응답 "
            "스키마를 반환하지 않았습니다."
        )

    response = PricingRecommendationResponse(
        final_price=final_price,
        pricing_reason=(
            result.pricing_reason
        ),
    )

    logger.info(
        "Pricing Agent 실행 완료 "
        "- final_price=%s",
        response.final_price,
    )

    return response