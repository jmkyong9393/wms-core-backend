import json
import os
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .state import Grade, WMSInspectionState

POLICY_VERSION = "UBCI_SPEC_V2.0.0.0"

load_dotenv()

# 신뢰도가 이 값보다 낮으면 자동 처리하지 않고 재검토
MIN_VISION_CONFIDENCE = 0.80
MIN_POLICY_CONFIDENCE = 0.75

NormalizedValue = Annotated[float, Field(ge=0, le=1)]

DefectCode = Literal[
    "COVER_SCRATCH",
    "COVER_TEAR",
    "STICKER_MARK",
    "CORNER_CRUSH",
    "EDGE_WEAR",
    "SPINE_CRACKING",
    "LOOSE_BINDING",
    "GENERAL_STAIN",
    "FADING",
    "SIGNATURE",
    "LIBRARY_STAMP",
    "WATER_DAMAGE",
    "PAGE_WARPING",
    "WRITING",
    "HIGHLIGHTING",
    "BARCODE_DAMAGE",
    "OTHER_VISIBLE_DAMAGE",
]

DefectLocation = Literal[
    "FRONT_COVER",
    "BACK_COVER",
    "SPINE",
    "CORNER",
    "BOOK_EDGE",
    "INNER_PAGE",
    "IDENTIFIER_AREA",
    "OTHER",
]


class DefectOutput(BaseModel):
    model_config = ConfigDict(strict=True)
    type: DefectCode
    location: DefectLocation
    bbox: list[NormalizedValue] = Field(min_length=4, max_length=4)
    ratio: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    image_index: int = Field(ge=0)
    text_overlap: bool = False      #결함이 표지나 본문의 글자를 실제로 가리는지 여부
    morphology_severe: bool = False # 재본 분리처럼 구조가 심하게 변형되었는지 여부

    @model_validator(mode="after")
    def validate_bbox(self):
        x_min, y_min, x_max, y_max = self.bbox

        if x_min >= x_max or y_min >= y_max:
            raise ValueError(
                "BBOX 좌표 순서가 올바르지 않습니다."
            )

        return self

class VisionOutput(BaseModel):
    #State에 저장하기 전 Vision 출력을 검증합니다.
    model_config = ConfigDict(strict=True)
    is_mint: bool
    defects: list[DefectOutput]
    image_quality_ok: bool
    vision_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_result(self):
        if not self.image_quality_ok:
            if self.is_mint or self.defects:
                raise ValueError(
                    "판독 불가 사진은 is_mint=False, defects=[]여야 합니다."
                )

            if self.vision_confidence >= MIN_VISION_CONFIDENCE:
                raise ValueError(
                    "판독 불가 사진의 신뢰도는 기준값보다 낮아야 합니다."
                )

            return self

        if self.is_mint == bool(self.defects):
            raise ValueError("is_mint와 defects가 서로 모순됩니다.")

        return self

    

    

def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    1. Vision Agent
    TODO: GPT-4o Vision API를 호출하여 이미지에서 BBox 추출 및 결함(Mint) 여부를 판단하세요.
    - 핵심: 사진 촬영 거리/구도에 영향을 받지 않도록, 전체 책 면적 대비 결함의 '상대 비율(Relative Ratio)'을 추출해야 합니다.
    - 입력: state["messages"] 내의 이미지 URL
    - 출력: is_mint (bool), defects (list of relative ratios)
    """
    print("[Agent] Vision Agent 실행...")

    prompt = """
    # 역할

    당신은 중고 도서 외관검수 전문 Vision Agent입니다.

    목표는 다음 두 가지입니다.

    1. 사진에서 직접 확인되는 물리적 결함만 판정합니다.
    2. 결함이 실제로 차지하는 영역을 원본 이미지 기준의
    정규화 BBox로 최대한 정확하게 기록합니다.

    정책·환불·등급·고객 귀책은 판단하지 않습니다.
    출력 스키마 이외의 설명은 반환하지 않습니다.

    # 판단 우선순위

    다음 순서를 반드시 지킵니다.

    1. 사진 품질을 확인합니다.
    2. 원본 이미지에서 실제 책 영역을 찾습니다.
    3. 책 영역을 순서대로 탐색합니다.
    4. 물리적 결함의 시각적 증거를 확인합니다.
    5. 결함 종류를 분류합니다.
    6. 결함 경계를 기준으로 BBox를 계산합니다.
    7. BBox를 다시 검증합니다.

    결함이 있다는 가정부터 시작하지 않습니다.
    BBox를 채우기 위해 결함이나 좌표를 추측하지 않습니다.

    # 사진 품질

    다음 이유로 결함 판독이나 위치 특정이 어렵다면:

    - 심한 초점 흐림
    - 모션 블러
    - 과도한 역광
    - 결함 후보 영역의 심한 반사
    - 책의 대부분이 가려짐
    - 지나치게 낮은 해상도

    다음과 같이 반환합니다.

    - image_quality_ok=false
    - is_mint=false
    - defects=[]
    - vision_confidence < 0.80

    사진 일부에 반사가 있더라도 다른 영역을 충분히 검사할 수 있다면
    전체 사진을 무조건 판독 불가로 처리하지 않습니다.

    # 결함 탐색 순서

    책 영역을 다음 순서로 빠짐없이 확인합니다.

    1. 앞표지 또는 뒷표지의 표면
    2. 위·아래·좌·우 가장자리
    3. 네 모서리
    4. 책등과 제본 경계
    5. 노출된 페이지와 페이지 단면
    6. 바코드·ISBN 식별 영역

    # 결함으로 인정할 수 있는 증거

    다음과 같은 물리적 변화가 눈으로 확인될 때만 결함으로 기록합니다.

    - 재질의 찢어짐, 갈라짐 또는 손실
    - 눌림, 접힘, 구겨짐 또는 비정상적인 휨
    - 표면의 실제 긁힘이나 마모
    - 인쇄물과 구별되는 얼룩, 필기 또는 도장
    - 제본부의 벌어짐이나 분리
    - 물에 젖어 발생한 변색 또는 변형

    다음은 결함으로 기록하지 않습니다.

    - 표지의 인쇄 글자, 그림, 로고, 테두리
    - 디자인상 음영, 그라데이션 또는 질감
    - 조명 반사, 그림자, 유광 광택
    - 비닐 커버의 정상적인 반사
    - 책상, 케이블, 다른 책 등 배경 물체
    - 카메라 원근으로 인한 정상적인 기울어짐
    - 실제 재질 변화가 확인되지 않는 단순한 색상 차이

    그림자나 반사만으로 휨·찢김을 추론하지 않습니다.

    # 결함 코드

    type은 허용된 표준 코드만 사용합니다.

    - COVER_SCRATCH: 표지의 실제 긁힘
    - COVER_TEAR: 표지 재질의 찢어짐
    - STICKER_MARK: 스티커 또는 라벨 제거 자국
    - CORNER_CRUSH: 모서리의 눌림, 찍힘 또는 구겨짐
    - EDGE_WEAR: 책 가장자리의 실제 마모
    - SPINE_CRACKING: 책등의 갈라짐
    - LOOSE_BINDING: 제본 벌어짐 또는 페이지 분리
    - GENERAL_STAIN: 일반적인 오염이나 얼룩
    - FADING: 빛바램 또는 변색
    - SIGNATURE: 서명 또는 이름
    - LIBRARY_STAMP: 도서관·장서인 도장
    - WATER_DAMAGE: 액체 흔적 또는 수침 손상
    - PAGE_WARPING: 페이지의 실제 뒤틀림
    - WRITING: 펜·연필 필기
    - HIGHLIGHTING: 형광펜 표시
    - BARCODE_DAMAGE: 바코드·ISBN 영역 훼손
    - OTHER_VISIBLE_DAMAGE: 물리적 훼손은 명확하지만 분류 불가

    # BBox 좌표 계약

    bbox는 반드시 다음 형식입니다.

    [x_min, y_min, x_max, y_max]

    좌표 기준은 책 crop이 아니라 배경을 포함한 원본 이미지 전체입니다.

    - 원본 이미지 왼쪽 위: (0.0, 0.0)
    - 원본 이미지 오른쪽 아래: (1.0, 1.0)
    - x는 왼쪽에서 오른쪽으로 증가
    - y는 위에서 아래로 증가

    BBox를 계산할 때 원본 이미지 위에 가상의
    1000 × 1000 좌표 격자가 있다고 생각합니다.

    1. 결함 증거의 가장 왼쪽 경계를 찾습니다.
    2. 가장 위쪽 경계를 찾습니다.
    3. 가장 오른쪽 경계를 찾습니다.
    4. 가장 아래쪽 경계를 찾습니다.
    5. 각 좌표를 1000으로 나누어 0~1로 정규화합니다.
    6. 좌표는 가능한 경우 소수점 셋째 자리까지 작성합니다.

    네 좌표를 각각 사진에서 독립적으로 계산합니다.
    미리 정해진 위치별 BBox를 재사용하지 않습니다.

    다음과 같은 고정형 좌표를 결함 위치 확인 없이 사용하지 않습니다.

    - [0.0, 0.0, 0.15, 0.15]
    - [0.75, 0.75, 0.95, 0.95]
    - [0.1, 0.1, 0.9, 0.9]

    실제 결함이 해당 위치와 정확히 일치할 때만 비슷한 값이
    나올 수 있습니다.

    # BBox 경계 규칙

    - BBox는 눈으로 확인되는 결함 전체를 포함해야 합니다.
    - 결함 주변의 정상 영역은 가능한 한 적게 포함합니다.
    - 책 전체를 감싸지 않습니다.
    - 배경을 감싸지 않습니다.
    - 모서리 눌림은 변형된 모서리와 연결된 주름까지만 감쌉니다.
    - 가장자리 마모는 마모가 확인되는 연속 구간만 감쌉니다.
    - 페이지 휨은 실제로 들리거나 굽은 페이지 영역만 감쌉니다.
    - 제본 벌어짐은 실제 간격이나 분리선이 보이는 영역만 감쌉니다.
    - 서로 떨어진 결함은 하나의 큰 BBox로 합치지 않습니다.
    - 결함이 이미지 경계에 실제로 닿을 때만 0 또는 1을 사용합니다.

    형태상 가능한 경우 BBox 중심 또는 주요 영역이 실제 결함 위에
    놓여야 합니다.

    결함 존재는 확인되지만 정확한 경계를 특정하기 어렵다면
    임의의 BBox를 만들지 말고 해당 defect의 confidence를 0.80 미만으로
    반환합니다.

    # Ratio

    ratio는 BBox 사각형 면적이 아닙니다.

    원본 사진에서 보이는 책 면적 대비 실제 결함 면적의 비율을
    0~100 float으로 추정합니다.

    정확히 판단하기 어렵다면 과장하지 않고 confidence를 낮춥니다.

    # 여러 이미지

    image_index는 입력된 이미지 순서이며 0부터 시작합니다.

    여러 사진에서 같은 물리적 결함이 반복되면 가장 선명하고
    경계가 명확한 사진의 결함 한 건만 반환합니다.

    다른 위치의 서로 다른 결함은 각각 반환합니다.

    # 출력 일관성

    - 명확한 결함이 없으면 is_mint=true, defects=[]
    - 결함이 하나 이상이면 is_mint=false
    - 판독 불가 사진은 is_mint=false, defects=[]
    - confidence와 vision_confidence는 0~1
    - text_overlap은 결함이 인쇄 글자를 실제로 가릴 때만 true
    - morphology_severe는 구조적 단절이나 심한 변형이 명확할 때만 true

    # 반환 전 내부 검증

    응답을 반환하기 전 다음을 확인합니다.

    - 결함을 인쇄 디자인이나 반사와 혼동하지 않았는가?
    - BBox 기준이 원본 이미지 전체인가?
    - x_min < x_max이고 y_min < y_max인가?
    - 모든 좌표가 0~1 범위인가?
    - BBox가 실제 결함 전체를 포함하는가?
    - 정상 영역과 배경을 과도하게 포함하지 않는가?
    - 위치별 고정 BBox를 추측해서 사용하지 않았는가?
    - 경계가 불확실한데 높은 confidence를 주지 않았는가?

    최종 출력은 지정된 구조화 스키마만 반환합니다.
    """

    messages = state.get("messages") or []
    image_message = None 
    image_count = 0
    
    #재검증 시 마지막 메시지는 critic의 AIMessage 일 수 있음
    for message in reversed(messages):
        content = getattr(message, "content", None)

        if not isinstance(content, list):
            continue

        image_items = [
            item
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "image_url"
        ]

        if image_items:
            image_message = message
            image_count = len(image_items)
            break

    #vision을 재실행하면 이전 policy, Critic, HITL 결과를 제거
    downstream_reset = {
        "ubci_score": None,
        "predicted_grade": None,
        "score_breakdown": None,
        "fatal_defect_detected": None,
        "grade_reason_code": None,
        "rule_reference": None,
        "policy_confidence": None,
        "overall_confidence": None,
        "human_feedback": None,
        "primary_reason_code": None,
        "target_grade": None,
        "final_grade": None,
        "final_report": None,
        }

    # 관리자 재촬영은 새로운 검사이므로 이전 실패 횟수를 초기화
    revision_count = (
        0
        if state.get("human_feedback") == "RE_CHECK"
        else state.get("revision_count", 0)
    )

    if type(revision_count) is not int or revision_count < 0:
        revision_count = 0

    def failure_result(message: str) -> WMSInspectionState:
        """Vision 실패 결과의 중복 작성을 줄이기 위한 내부 함수입니다."""
        return {
            **downstream_reset,
            "is_mint": None,
            "defects": None,
            "image_quality_ok": False,
            "vision_confidence": None,
            "reason_code": "QUALITY_ERROR",
            "repair_directive": message,
            "revision_count": revision_count + 1,
            "messages": [
                AIMessage(content=f"[Vision Agent] 실패 - {message}")
            ],
        }

    if image_message is None:
        return failure_result("검수할 이미지 URL이 없습니다.")

    try:
        vision_model = ChatOpenAI(
            model=os.getenv("VISION_MODEL", "gpt-4o"),
            temperature=0,
            timeout=30,
            max_retries=1,
        ).with_structured_output(
            VisionOutput,
            method="json_schema",
        )

        result = vision_model.invoke([
            ("system", prompt),
            image_message,
        ])

        if result is None:
            raise ValueError("Vision 모델 응답이 없습니다.")

        # 입력이 두 장인데 image_index=5 같은 값이 나오는 것을 방어
        if any(
            defect.image_index >= image_count
            for defect in result.defects
        ):
            raise ValueError(
                "존재하지 않는 image_index가 반환되었습니다."
            )

    except Exception as error:
        print(
            "[Agent] Vision Agent 호출 실패:",
            type(error).__name__,
        )
        return failure_result(
            "Vision 모델 호출 또는 출력 검증에 실패했습니다."
        )

    reason_code = None
    repair_directive = None

    # 전체 또는 개별 신뢰도가 낮으면 재촬영/HITL 대상으로 표시
    if (
        not result.image_quality_ok
        or result.vision_confidence < MIN_VISION_CONFIDENCE
        or any(
            defect.confidence < MIN_VISION_CONFIDENCE
            for defect in result.defects
        )
    ):
        reason_code = "VISION_LOW_CONFIDENCE"
        repair_directive = (
            "사진 품질 또는 결함 판정 신뢰도가 기준보다 낮습니다."
        )
        revision_count += 1

    # 분류할 수 없는 결함은 반복 실행보다 관리자 확인이 적절
    elif any(
        defect.type == "OTHER_VISIBLE_DAMAGE"
        for defect in result.defects
    ):
        reason_code = "VISION_UNCLASSIFIED_DEFECT"
        repair_directive = (
            "표준 코드로 분류할 수 없는 결함을 관리자가 확인해야 합니다."
        )

    return {
        **downstream_reset,
        "is_mint": result.is_mint,
        "defects": [
            defect.model_dump()
            for defect in result.defects
        ],
        "image_quality_ok": result.image_quality_ok,
        "vision_confidence": result.vision_confidence,
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "messages": [
            AIMessage(
                content=(
                    f"[Vision Agent] 판독 결과 - "
                    f"{reason_code or '정상'}"
                )
            )
        ],
    }


PENALTY_MATRIX: dict[
    str,
    tuple[int | None, int | None, int | None],
] = {
    "COVER_SCRATCH": (2, 5, 10),
    "COVER_TEAR": (5, 10, 15),
    "STICKER_MARK": (2, 3, 5),
    "CORNER_CRUSH": (3, 5, 10),
    "EDGE_WEAR": (2, 4, 7),
    "SPINE_CRACKING": (0, 5, 10),
    "LOOSE_BINDING": (0, 10, None),
    "GENERAL_STAIN": (2, 5, 8),
    "FADING": (3, 6, 10),
    "SIGNATURE": (0, 10, 10),
    "LIBRARY_STAMP": (0, 15, 15),
}

# 면적과 무관하게 즉시 반려되는 결함
FATAL_DEFECTS = {
    "WATER_DAMAGE",
    "PAGE_WARPING",
}

# 사진만으로 페이지 수나 실제 상태를 확정하기 어려운 결함
HITL_REQUIRED_DEFECTS = {
    "WRITING",
    "HIGHLIGHTING",
    "BARCODE_DAMAGE",
    "OTHER_VISIBLE_DAMAGE",
}


def get_severity(ratio: float) -> tuple[int, str]:
    """결함 면적을 심각도 구간으로 변환합니다."""
    if ratio < 5:
        return 0, "MINOR"

    if ratio < 15:
        return 1, "MODERATE"

    return 2, "SEVERE"



def calculate_ubci_score(
   defects: list[dict],) -> tuple[float, list[dict], bool]:
    """
    동일 결함의 면적을 합산하여 다음 값을 반환합니다.

    1. UBCI 점수
    2. 결함별 감점 내역
    3. 치명 결함 존재 여부
    """
    grouped_defects: dict[str, dict] = {}

    for defect in defects:
        if type(defect) is not dict:
            raise ValueError(
                "defects의 각 항목은 dict여야 합니다."
            )

        defect_type = defect.get("type")
        ratio = defect.get("ratio")
        text_overlap = defect.get("text_overlap", False)
        morphology_severe = defect.get(
            "morphology_severe",
            False,
        )

        if defect_type in HITL_REQUIRED_DEFECTS:
            raise ValueError(
                f"관리자 확인이 필요한 결함입니다: {defect_type}"
            )

        if (
            defect_type not in PENALTY_MATRIX
            and defect_type not in FATAL_DEFECTS
        ):
            raise ValueError(
                f"UBCI v2에 정의되지 않은 결함입니다: {defect_type}"
            )

        if (
            type(ratio) not in (int, float)
            or not 0 <= ratio <= 100
        ):
            raise ValueError(
                "결함 ratio는 0~100 범위의 숫자여야 합니다."
            )

        if type(text_overlap) is not bool:
            raise ValueError(
                "text_overlap은 bool이어야 합니다."
            )

        if type(morphology_severe) is not bool:
            raise ValueError(
                "morphology_severe는 bool이어야 합니다."
            )

        grouped = grouped_defects.setdefault(
            defect_type,
            {
                "ratio": 0.0,
                "text_overlap": False,
                "morphology_severe": False,
            },
        )

        # 같은 종류의 결함이 여러 개라면 면적을 합산
        grouped["ratio"] += float(ratio)
        grouped["text_overlap"] |= text_overlap
        grouped["morphology_severe"] |= morphology_severe

    score_breakdown = []
    total_penalty = 0.0
    fatal_defect_detected = False

    for defect_type, defect in grouped_defects.items():
        total_ratio = min(defect["ratio"], 100.0)
        severity_index, severity = get_severity(total_ratio)

        is_fatal = defect_type in FATAL_DEFECTS

        # 심각한 제본 벌어짐도 즉시 반려
        if defect_type == "LOOSE_BINDING":
            is_fatal = (
                severity == "SEVERE"
                or defect["morphology_severe"]
            )

        if is_fatal:
            fatal_defect_detected = True

            score_breakdown.append({
                "type": defect_type,
                "total_ratio": total_ratio,
                "severity": severity,
                "text_overlap": defect["text_overlap"],
                "applied_penalty": None,
                "fatal": True,
            })
            continue

        base_penalty = PENALTY_MATRIX[
            defect_type
        ][severity_index]

        # 텍스트를 침범한 결함에는 1.5배를 적용
        multiplier = (
            1.5
            if defect["text_overlap"]
            else 1.0
        )

        # Python의 짝수 반올림을 피하고 일반적인 0.5 올림을 사용
        applied_penalty = round(
            base_penalty * multiplier,1
        )

        total_penalty += applied_penalty

        score_breakdown.append({
            "type": defect_type,
            "total_ratio": total_ratio,
            "severity": severity,
            "text_overlap": defect["text_overlap"],
            "applied_penalty": applied_penalty,
            "fatal": False,
        })

    ubci_score = (
        0.0
        if fatal_defect_detected
        else round(max(0.0, 100 - total_penalty),1)
    )

    return (
        ubci_score,
        score_breakdown,
        fatal_defect_detected,
    )

def calculate_ubci_grade(
    ubci_score: float,
    fatal_defect_detected: bool = False,
) -> Grade:
    """UBCI v2 경계값을 사용해 등급을 계산합니다."""
    if (
        type(ubci_score) not in (int, float)
        or not 0 <= ubci_score <= 100
    ):
        raise ValueError(
            "ubci_score는 0~100 범위의 정수여야 합니다."
        )
    
    ubci_score = float(ubci_score)

    if fatal_defect_detected or ubci_score < 65:
        return "REJECT"

    if ubci_score >= 95:
        return "S"

    if ubci_score >= 85:
        return "A"

    return "B"


def policy_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    2. Policy Agent (RAG 적용)
    TODO: Vision이 넘겨준 상대 비율(예: 가로 15% 찢김)을 바탕으로 Vector DB(RAG)를 검색하여 UBCI 규정을 찾아오세요.
    - 핵심: RAG로 검색된 규정(ex. 10~20% 찢김 감점)을 기반으로 수학적인 감점 점수를 계산하고 사유를 작성합니다.
    - 입력: state["defects"] (상대 비율 데이터)
    - 출력: ubci_score (int), rule_reference (str)
    """
    print("[Agent] Policy Agent 실행...")

    defects = state.get("defects") or []
    raw_revision_count = state.get(
        "revision_count",
        0,
    )
    revision_count = (
        raw_revision_count
        if type(raw_revision_count) is int
        and raw_revision_count >= 0
        else 0
    )


    try:
        if not defects:
            raise ValueError(
                "Policy Agent에 전달된 결함이 없습니다."
            )

        if any(type(defect) is not dict for defect in defects):
            raise ValueError(
                "defects의 각 항목은 dict여야 합니다."
            )

        manual_types = sorted(
            {
                defect.get("type")
                for defect in defects
            }
            & HITL_REQUIRED_DEFECTS
        )

        # 재계산해도 해결되지 않는 항목이므로 바로 HITL로 보냄
        if manual_types:
            return {
                "ubci_score": None,
                "predicted_grade": None,
                "score_breakdown": None,
                "fatal_defect_detected": None,
                "grade_reason_code": manual_types[0],
                "rule_reference": POLICY_VERSION,
                "policy_confidence": None,
                "reason_code": "POLICY_REQUIRES_HITL",
                "repair_directive": (
                    "관리자 확인 필요 결함: "
                    + ", ".join(manual_types)
                ),
                "revision_count": revision_count,
                "overall_confidence": None,
                "human_feedback": None,
                "final_grade": None,
                "final_report": None,
                "messages": [
                    AIMessage(
                        content=(
                            "[Policy Agent] 관리자 확인 필요"
                        )
                    )
                ],
            }

        (
            ubci_score,
            score_breakdown,
            fatal_defect_detected,
        ) = calculate_ubci_score(defects)

        predicted_grade = calculate_ubci_grade(
            ubci_score,
            fatal_defect_detected,
        )

        if fatal_defect_detected:
            grade_reason_code = next(
                item["type"]
                for item in score_breakdown
                if item["fatal"]
            )
        else:
            grade_reason_code = max(
                score_breakdown,
                key=lambda item: item["applied_penalty"],
            )["type"]

        result = {
            "ubci_score": ubci_score,
            "predicted_grade": predicted_grade,
            "score_breakdown": score_breakdown,
            "fatal_defect_detected": fatal_defect_detected,
            "grade_reason_code": grade_reason_code,
            "rule_reference": POLICY_VERSION,

            # 현재는 고정된 정책표 계산 신뢰도
            # RAG 연결 후 검색 신뢰도로 교체
            "policy_confidence": 1.0,
            "reason_code": None,
            "repair_directive": None,
        }

    except (TypeError, ValueError) as error:
        revision_count += 1

        result = {
            "ubci_score": None,
            "predicted_grade": None,
            "score_breakdown": None,
            "fatal_defect_detected": None,
            "grade_reason_code": None,
            "rule_reference": None,
            "policy_confidence": None,
            "reason_code": "UBCI_POLICY_VIOLATION",
            "repair_directive": str(error),
        }

    return {
        **result,
        "revision_count": revision_count,
        "overall_confidence": None,
        "human_feedback": None,
        "final_grade": None,
        "final_report": None,
        "messages": [
            AIMessage(
                content=(
                    "[Policy Agent] 계산 결과 - "
                    f"{result['reason_code'] or '정상'}"
                )
            )
        ],
    }


def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    3. Critic Agent
    TODO: Policy Agent가 RAG에서 가져온 규정과 실제 감점 연산이 타당한지 교차 검증하세요.
    - 핵심: 검증 실패 시 Policy로 되돌려보냅니다. 여러 번 실패할 경우 HITL(수동 개입)으로 에스컬레이션합니다.
    - 출력: reason_code ("OK", "REJECT"), revision_count 증가
    """
    print("[Agent] Critic Agent 실행...")

    raw_revision_count = state.get(
        "revision_count",
        0,
    )
    revision_count = (
        raw_revision_count
        if (
            type(raw_revision_count) is int
            and raw_revision_count >= 0
        )
        else 0
    )

    is_mint = state.get("is_mint")
    defects = state.get("defects")
    vision_confidence = state.get(
        "vision_confidence"
    )
    ubci_score = state.get("ubci_score")
    rule_reference = state.get(
        "rule_reference"
    )
    policy_confidence = state.get(
        "policy_confidence"
    )

    reason_code = "OK"
    repair_directive = None
    overall_confidence = None

    if (
        type(raw_revision_count) is not int
        or raw_revision_count < 0
    ):
        reason_code = "QUALITY_ERROR"
        repair_directive = (
            "revision_count는 0 이상의 정수여야 합니다."
        )

    # Vision 출력 타입 검증
    elif type(is_mint) is not bool:
        reason_code = "QUALITY_ERROR"
        repair_directive = "is_mint는 bool이어야 합니다."

    elif type(defects) is not list:
        reason_code = "QUALITY_ERROR"
        repair_directive = "defects는 list여야 합니다."

    # 각 결함의 필수 값 검증
    else:
        for defect in defects:
            if type(defect) is not dict:
                reason_code = "QUALITY_ERROR"
                repair_directive = "defects의 각 항목은 dict여야 합니다."
                break

            defect_type = defect.get("type")
            ratio = defect.get("ratio")

            if type(defect_type) is not str or not defect_type.strip():
                reason_code = "QUALITY_ERROR"
                repair_directive = "결함 type은 비어 있지 않은 문자열이어야 합니다."
                break

            if type(ratio) not in (int, float) or not 0 <= ratio <= 100:
                reason_code = "QUALITY_ERROR"
                repair_directive = "결함 ratio는 0~100 범위의 숫자여야 합니다."
                break

    # Vision 판정과 결함 데이터의 모순 검증
    if reason_code == "OK" and is_mint is True and defects:
        reason_code = "VISION_RESULT_CONFLICT"
        repair_directive = "MINT 판정과 결함 데이터가 서로 모순됩니다."

    elif reason_code == "OK" and is_mint is False and not defects:
        reason_code = "VISION_RESULT_CONFLICT"
        repair_directive = "비정상품 판정에는 한 개 이상의 결함이 필요합니다."

    # Vision 신뢰도 검증
    elif reason_code == "OK" and (type(vision_confidence) not in (int, float) or not 0 <= vision_confidence <= 1):
        reason_code = "QUALITY_ERROR"
        repair_directive = "vision_confidence는 0~1 범위의 숫자여야 합니다."

    elif reason_code == "OK" and vision_confidence < MIN_VISION_CONFIDENCE:
        reason_code = "VISION_LOW_CONFIDENCE"
        repair_directive = "Vision 판정 신뢰도가 기준보다 낮습니다."

    # UBCI 점수 검증
    elif reason_code == "OK" and (type(ubci_score) not in (int,float) or not 0 <= ubci_score <= 100):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "ubci_score는 0~100 범위의 숫자여야 합니다."

    # 정책 근거 검증
    elif reason_code == "OK" and (type(rule_reference) is not str or not rule_reference.strip()):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "rule_reference는 비어 있지 않은 문자열이어야 합니다."

    # Policy 신뢰도 검증
    elif reason_code == "OK" and (type(policy_confidence) not in (int, float) or not 0 <= policy_confidence <= 1):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "policy_confidence는 0~1 범위의 숫자여야 합니다."

    elif reason_code == "OK" and policy_confidence < MIN_POLICY_CONFIDENCE:
        reason_code = "POLICY_LOW_CONFIDENCE"
        repair_directive = "Policy 검색 및 계산 신뢰도가 기준보다 낮습니다."

    if reason_code == "OK":
        overall_confidence = min(vision_confidence, policy_confidence)
    else:
        revision_count += 1

    return {
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "overall_confidence": overall_confidence,
        "final_report": None,
        "messages": [
            AIMessage(content=f"[Critic Agent] 검증 결과 - {reason_code}")
        ],
    }

def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    4. Auto-Refund Agent (Fast-track)
    TODO: MINT 등급의 새 책에 대한 환불 승인 사유서(JSON)를 작성하세요.
    - 출력: final_report (str, JSON format)
    """
    print("[Agent] Auto Refund Agent 스켈레톤 로직 실행...")
    dummy_report = {
        "result": "AUTO_REFUND_APPROVED",
        "reason": "MINT 자동 승인",
        "vision_confidence": state.get("vision_confidence"),
    }

    return {
        "final_report": json.dumps(dummy_report, ensure_ascii=False),
        "overall_confidence": state.get("vision_confidence"),
        "human_feedback": None,
        "messages": [
            AIMessage(content="[Auto Refund Agent] 자동 환불 승인 리포트 생성 완료")
        ],
    }

def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    5. Report Agent (감성/페르소나 렌더링)
    TODO: 검증이 완료된 기술적 사유를 바탕으로, 상황에 맞는 CS 페르소나(Tone & Manner)를 입혀 고객용 보증서를 생성하세요.
    - 핵심: 파이썬 단순 문자열 조합이 아닌, 결함의 심각도(가벼운 오염 vs 심각한 파손)에 따라 동적으로 다정한 위로나 단호한 매입 불가 안내를 작성해야 LLM을 사용하는 명분이 생깁니다.
    - 출력: final_report (str, JSON format)
    """
    print("[Agent] Report Agent 실행...")

    human_feedback = state.get("human_feedback")
    predicted_grade = state.get("predicted_grade")
    target_grade = state.get("target_grade")
    primary_reason_code = state.get(
        "primary_reason_code"
    )

    if human_feedback is None:
        if state.get("reason_code") != "OK":
            raise ValueError(
                "AI 자동 보고서는 Critic OK 이후에만 생성할 수 있습니다."
            )

        if predicted_grade not in {
            "S",
            "A",
            "B",
            "REJECT",
        }:
            raise ValueError(
                "유효한 predicted_grade가 필요합니다."
            )

        result = "INSPECTION_COMPLETED"
        message = "AI 검수 완료"
        final_grade = predicted_grade

    elif human_feedback == "APPROVE_NORMAL":
        result = "HUMAN_APPROVED_NORMAL"
        message = "관리자 정상 승인 완료"
        final_grade = "S"

    elif human_feedback == "APPROVE_DOWNGRADE":
        if target_grade not in {"A", "B"}:
            raise ValueError(
                "등급 하향 승인에는 A/B target_grade가 필요합니다."
            )

        if (
            type(primary_reason_code) is not str
            or not primary_reason_code.strip()
        ):
            raise ValueError(
                "등급 하향 승인에는 primary_reason_code가 필요합니다."
            )

        result = "HUMAN_APPROVED_DOWNGRADE"
        message = (
            f"관리자 등급 하향 승인 완료: "
            f"{target_grade}등급"
        )
        final_grade = target_grade

    elif human_feedback == "REJECT_RETURN":
        if (
            type(primary_reason_code) is not str
            or not primary_reason_code.strip()
        ):
            raise ValueError(
                "반품에는 primary_reason_code가 필요합니다."
            )

        result = "HUMAN_REJECTED_RETURN"
        message = "관리자 반품 결정 완료"
        final_grade = "REJECT"

    elif human_feedback == "REJECT_DISCARD":
        if (
            type(primary_reason_code) is not str
            or not primary_reason_code.strip()
        ):
            raise ValueError(
                "폐기에는 primary_reason_code가 필요합니다."
            )

        result = "HUMAN_REJECTED_DISCARD"
        message = "관리자 폐기 결정 완료"
        final_grade = "REJECT"

    elif human_feedback == "RE_CHECK":
        raise ValueError(
            "RE_CHECK는 Report가 아니라 Vision으로 이동해야 합니다."
        )

    else:
        raise ValueError(
            f"허용되지 않은 human_feedback입니다: "
            f"{human_feedback!r}"
        )

    report = {
        "result": result,
        "decision": (
            human_feedback
            if human_feedback is not None
            else "AI_INSPECTION"
        ),
        "defects": state.get("defects") or [],
        "ubci_score": state.get("ubci_score"),
        "predicted_grade": predicted_grade,
        "final_grade": final_grade,
        "score_breakdown": (
            state.get("score_breakdown") or []
        ),
        "fatal_defect_detected": state.get(
            "fatal_defect_detected"
        ),
        "grade_reason_code": state.get(
            "grade_reason_code"
        ),
        "primary_reason_code": primary_reason_code,
        "target_grade": target_grade,
        "rule_reference": state.get(
            "rule_reference"
        ),
        "reason_code": state.get("reason_code"),
        "vision_confidence": state.get(
            "vision_confidence"
        ),
        "policy_confidence": state.get(
            "policy_confidence"
        ),
        "overall_confidence": state.get(
            "overall_confidence"
        ),
        "message": message,
    }

    return {
        "final_grade": final_grade,
        "final_report": json.dumps(
            report,
            ensure_ascii=False,
        ),
        "human_feedback": None,
        "messages": [
            AIMessage(
                content=f"[Report Agent] {message}"
            )
        ],
    }

def human_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    6. Human-In-The-Loop (HITL) 노드
    TODO: Critic이 반복해서 Policy를 반려하거나 확신할 수 없는 예외 케이스(Outlier)일 경우, 관리자의 수동 개입을 대기합니다.
    - 주의: 이 노드는 MemorySaver에 의해 일시 정지(Pause)를 유발하는 용도이므로 빈 상태로 둡니다.
    """
    print("[Agent] HITL 노드 진입 - 관리자의 수동 개입(승인/수정) 대기 중")
    return {}
