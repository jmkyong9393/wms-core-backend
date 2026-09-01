import logging
import os
from decimal import (
    ROUND_CEILING,
    ROUND_HALF_UP,
    Decimal,
)

from dotenv import load_dotenv
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from langchain_openai import ChatOpenAI

from app.domains.pricing.schemas.pricing import (
    PricingReason,
    PricingRecommendationRequest,
    PricingRecommendationResponse,
)

load_dotenv()

logger = logging.getLogger(__name__)


# 실제 거래 데이터 축적 전 적용할 초기 가격 정책
PRICING_POLICY_VERSION = "PRICING_POLICY_V1"


# 실제 거래 데이터 축적 전 적용하는 V1 카테고리 보존계수
CATEGORY_POLICY: dict[
    str,
    tuple[str, Decimal],
] = {
    "COMIC": (
        "만화·웹툰",
        Decimal("0.78"),
    ),
    "STUDY_GUIDE": (
        "수험서·참고서",
        Decimal("0.74"),
    ),
    "NOVEL": (
        "소설·시·희곡",
        Decimal("0.76"),
    ),
    "HUMANITIES": (
        "인문·역사·철학",
        Decimal("0.66"),
    ),
    "SOCIAL_SCIENCE": (
        "사회과학",
        Decimal("0.64"),
    ),
    "BUSINESS_ECONOMICS": (
        "경제·경영",
        Decimal("0.69"),
    ),
    "SCIENCE_TECHNOLOGY": (
        "과학·기술",
        Decimal("0.71"),
    ),
    "CHILDREN": (
        "유아·어린이·청소년",
        Decimal("0.68"),
    ),
    "LANGUAGE": (
        "외국어·어학",
        Decimal("0.72"),
    ),
    "ART_LIFESTYLE": (
        "예술·생활",
        Decimal("0.65"),
    ),
}


def round_to_hundred(
    price: Decimal,
) -> int:
    """가격의 100원 단위 반올림."""

    rounded = (price / Decimal("100")).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )

    return int(rounded * Decimal("100"))


def calculate_discount_rate(
    base_price: Decimal,
    final_price: int,
) -> int:
    """정가 대비 정수 할인율 계산."""

    final_price_decimal = Decimal(final_price)

    # 잘못된 가격 관계 차단
    if base_price <= 0 or final_price < 0 or final_price_decimal > base_price:
        raise ValueError("최종 가격은 0 이상이며 정가 이하여야 합니다.")

    discount_rate = (base_price - final_price_decimal) / base_price * Decimal("100")

    # 백엔드 요구에 따른 정수 할인율 올림
    return int(
        discount_rate.quantize(
            Decimal("1"),
            rounding=ROUND_CEILING,
        )
    )


def calculate_condition_retention(
    ubci_score: float,
) -> Decimal:
    """UBCI 등급 구간 기반 상태 보존계수 계산."""

    score = Decimal(str(ubci_score))

    # 가격 정책의 UBCI 허용 범위 검증
    if score < Decimal("65") or score > Decimal("100"):
        raise ValueError("UBCI 점수는 65 이상 100 이하이어야 합니다.")

    # S등급 상태 보존계수
    if score >= Decimal("95"):
        return Decimal("0.95") + (score - Decimal("95")) * Decimal("0.01")

    # A등급 상태 보존계수
    if score >= Decimal("85"):
        return Decimal("0.75") + (score - Decimal("85")) * Decimal("0.02")

    # B등급 상태 보존계수
    return Decimal("0.45") + (score - Decimal("65")) * Decimal("0.015")


def calculate_final_price(
    request: PricingRecommendationRequest,
) -> tuple[int, Decimal, str]:
    """카테고리와 UBCI 기반 최종 가격 계산."""

    category_key = request.category

    category_label, category_retention = CATEGORY_POLICY[category_key]

    # UBCI 등급 구간 기반 상태 보존계수 계산
    condition_retention = calculate_condition_retention(request.ubci_score)

    # 카테고리와 도서 상태를 반영한 반올림 전 가격 계산
    raw_price = request.base_price * category_retention * condition_retention

    # 무료 가격 방지를 위한 최소 판매가격 보장
    final_price = max(
        100,
        round_to_hundred(raw_price),
    )

    # 가격 정책 검증을 위한 계산 근거 기록
    logger.info(
        "Pricing 계산 완료 "
        "- policy=%s, base_price=%s, "
        "category=%s, category_retention=%s, "
        "ubci_score=%s, condition_retention=%s, "
        "raw_price=%s, final_price=%s",
        PRICING_POLICY_VERSION,
        request.base_price,
        category_key,
        category_retention,
        request.ubci_score,
        condition_retention,
        raw_price,
        final_price,
    )

    return (
        final_price,
        raw_price,
        category_label,
    )


def build_fallback_reason(
    request: PricingRecommendationRequest,
    category_label: str,
    final_price: int,
) -> str:
    """LLM 장애 시 사용할 기본 가격 선정 사유."""

    return (
        f"{category_label} 카테고리에 적용되는 가격 정책과 "
        f"UBCI {request.ubci_score:g}점의 도서 보존 상태를 "
        "반영했습니다. "
        f"검증된 가격 계산 결과에 따라 최종 판매가격을 "
        f"{final_price:,}원으로 책정했습니다."
    )


def generate_pricing_reason(
    request: PricingRecommendationRequest,
    category_label: str,
    final_price: int,
) -> str:
    """확정 가격에 대한 LLM 선정 사유 생성."""

    fallback_reason = build_fallback_reason(
        request,
        category_label,
        final_price,
    )

    api_key = os.getenv(
        "OPENAI_API_KEY",
        "",
    ).strip()

    # API 키 누락 시 가격 응답 유지를 위한 기본 사유 반환
    if not api_key:
        logger.warning("Pricing 사유 생성 생략 - OPENAI_API_KEY 누락")
        return fallback_reason

    llm = ChatOpenAI(
        model=os.getenv(
            "OPENAI_MODEL",
            "gpt-4o-mini",
        ),
        temperature=0,
        timeout=10,
        max_retries=1,
        api_key=api_key,
    )

    # LLM의 JSON Schema 형식 준수
    structured_llm = llm.with_structured_output(
        PricingReason,
        method="json_schema",
        strict=True,
    )

    messages = [
        SystemMessage(
            content=(
                "당신은 WMS 중고 도서의 가격 선정 사유를 "
                "작성하는 Pricing Agent입니다.\n\n"
                "[역할]\n"
                "가격은 이미 Python 가격 정책 로직으로 "
                "확정되었습니다. 제공된 사실만 사용하여 "
                "가격 선정 사유를 작성하세요.\n\n"
                "[금지 사항]\n"
                "1. 최종 가격을 계산하거나 변경하지 마세요.\n"
                "2. 입력에 없는 시장 수요, 판매량, 희소성, "
                "인기도를 추측하지 마세요.\n"
                "3. UBCI 점수와 품질 등급을 변경하지 마세요.\n"
                "4. 새로운 할인율이나 가격 정책을 "
                "만들지 마세요.\n"
                "5. 제공되지 않은 도서 결함을 "
                "추가하지 마세요.\n\n"
                "[응답 규칙]\n"
                "1. 카테고리, UBCI 점수, 품질 등급, "
                "확정 가격만 근거로 사용하세요.\n"
                "2. 소비자와 관리자가 이해할 수 있는 "
                "한국어 두 문장으로 작성하세요.\n"
                "3. 지정된 JSON Schema 형식으로만 "
                "응답하세요.\n"
                "4. UBCI 점수를 높음 또는 낮음으로 새롭게 평가하지 말고 "
                "제공된 숫자를 그대로 표현하세요.\n"
                "5. 가격을 직접 결정했다는 표현 대신 Python 정책 계산으로 "
                "확정된 가격이라는 점을 설명하세요."
            )
        ),
        HumanMessage(
            content=(
                "[검증된 가격 정보]\n"
                f"- 카테고리: {category_label}\n"
                f"- UBCI 점수: {request.ubci_score:g}\n"
                f"- 품질 등급: "
                f"{request.condition_grade.value}\n"
                f"- 확정 최종 가격: {final_price:,}원\n\n"
                "위 정보만 사용하여 가격 선정 사유를 "
                "한국어 두 문장으로 작성하세요."
            )
        ),
    ]

    try:
        result = structured_llm.invoke(messages)

        if not isinstance(
            result,
            PricingReason,
        ):
            raise TypeError("Pricing Agent 응답 스키마 오류")

        return result.pricing_reason

    # LLM 장애가 가격 계산 실패로 전파되는 상황 방지
    except Exception:
        logger.exception("Pricing 사유 생성 실패 - 기본 사유 반환")
        return fallback_reason


def pricing_agent(
    request: PricingRecommendationRequest,
) -> PricingRecommendationResponse:
    """최종 가격 계산과 LLM 선정 사유 생성."""

    final_price, _, category_label = calculate_final_price(request)

    discount_rate = calculate_discount_rate(
        request.base_price,
        final_price,
    )

    pricing_reason = generate_pricing_reason(
        request,
        category_label,
        final_price,
    )

    response = PricingRecommendationResponse(
        final_price=final_price,
        discount_rate=discount_rate,
        pricing_reason=pricing_reason,
    )

    logger.info(
        "Pricing Agent 실행 완료 - final_price=%s, discount_rate=%s",
        response.final_price,
        response.discount_rate,
    )

    return response
