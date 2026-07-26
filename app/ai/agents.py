import json, base64
import os
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .state import Grade, WMSInspectionState
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw
from langchain_core.messages import AIMessage, HumanMessage
from ultralytics import YOLO

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

# VLM이 승인한 최종 결함의 데이터 계약
# 정규화 BBox가 0~1 범위인지 검증하고 좌표가 뒤집힌 결과를 차단
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


RejectReason = Literal[
    "BACKGROUND_OBJECT",
    "COVER_DESIGN",
    "LIGHT_OR_SHADOW",
    "INSUFFICIENT_EVIDENCE",
]

# YOLO 후보 하나에 대한 VLM의 승인 또는 거절 결과
# 승인된 후보와 거절된 후보의 필수 필드가 섞이지 않도록 검증
class CandidateReview(BaseModel):
    model_config = ConfigDict(strict=True)

    candidate_id: int = Field(ge=0)
    is_defect: bool
    type: DefectCode | None = None
    location: DefectLocation | None = None
    ratio: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    reject_reason: RejectReason | None = None
    text_overlap: bool = False
    morphology_severe: bool = False

    @model_validator(mode="after")
    def validate_review(self):
        if self.is_defect:
            if self.type is None or self.location is None:
                raise ValueError(
                    "실제 결함은 type과 location이 필요합니다."
                )

            if self.reject_reason is not None:
                raise ValueError(
                    "실제 결함에 reject_reason을 사용할 수 없습니다."
                )

            if self.ratio <= 0:
                raise ValueError(
                    "실제 결함의 ratio는 0보다 커야 합니다."
                )

        else:
            if self.type is not None or self.location is not None:
                raise ValueError(
                    "거절 후보에는 결함 유형과 위치를 지정하지 않습니다."
                )

            if self.ratio != 0:
                raise ValueError(
                    "거절 후보의 ratio는 0이어야 합니다."
                )

            if self.reject_reason is None:
                raise ValueError(
                    "거절 후보에는 reject_reason이 필요합니다."
                )

        return self

# 사진 한 장에 포함된 모든 YOLO 후보의 VLM 검증 결과
# YOLO가 놓친 결함 의심 여부와 사진 전체 신뢰도도 함께 전달
class HybridVisionReview(BaseModel):
    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    reviews: list[CandidateReview]
    missed_defect_suspected: bool
    vision_confidence: float = Field(ge=0, le=1)
YOLO_IMAGE_SIZE = 960
YOLO_MAX_CANDIDATES = 10

# YOLO 모델은 로딩 비용이 크므로 프로세스에서 한 번만 로드해 재사용
# 상대 경로는 프로젝트 루트를 기준으로 변환하고 모델이 없으면 즉시 실패시킴
@lru_cache(maxsize=1)
def get_yolo_model() -> YOLO:
    repo_root = Path(__file__).resolve().parents[2]
    configured_path = Path(
        os.getenv(
            "YOLO_MODEL_PATH",
            "models/defect_region_best.pt",
        )
    )

    model_path = (
        configured_path
        if configured_path.is_absolute()
        else repo_root / configured_path
    )

    if not model_path.exists():
        raise FileNotFoundError(
            f"YOLO 모델이 없습니다: {model_path}"
        )

    return YOLO(str(model_path))

# PIL 이미지를 임시 파일 없이 Base64 Data URL로 변환해 OpenAI API에 전달
def image_to_data_url(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(
        buffer,
        format="JPEG",
        quality=90,
    )

    encoded = base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"

# YOLO는 결함 종류를 판정하지 않고 의심 영역만 찾음
# 좌표를 이미지 경계 안으로 제한하고 후보 수를 10개로 제한해 오탐과 API 비용을 방어
# VLM 검증용 Crop에는 주변 문맥을 15% 포함
def detect_yolo_candidates(
    image: Image.Image,
) -> list[dict]:
    model = get_yolo_model()

    result = model.predict(
        source=image,
        conf=float(
            os.getenv("YOLO_CONFIDENCE", "0.25")
        ),
        iou=float(
            os.getenv("YOLO_IOU", "0.50")
        ),
        imgsz=YOLO_IMAGE_SIZE,
        max_det=YOLO_MAX_CANDIDATES,
        device=os.getenv("YOLO_DEVICE", "cpu"),
        verbose=False,
    )[0]

    if result.boxes is None:
        return []

    width, height = image.size
    boxes = result.boxes.xyxy.cpu().tolist()
    confidences = result.boxes.conf.cpu().tolist()

    candidates = []

    for candidate_id, (box, confidence) in enumerate(
        zip(boxes, confidences)
    ):
        x1, y1, x2, y2 = box

        x1 = max(0, min(int(x1), width - 1))
        y1 = max(0, min(int(y1), height - 1))
        x2 = max(x1 + 1, min(int(x2), width))
        y2 = max(y1 + 1, min(int(y2), height))

        # Crop에 주변 문맥을 조금 포함한다.
        padding_x = int((x2 - x1) * 0.15)
        padding_y = int((y2 - y1) * 0.15)

        crop = image.crop((
            max(0, x1 - padding_x),
            max(0, y1 - padding_y),
            min(width, x2 + padding_x),
            min(height, y2 + padding_y),
        ))

        candidates.append({
            "candidate_id": candidate_id,
            "bbox": [
                x1 / width,
                y1 / height,
                x2 / width,
                y2 / height,
            ],
            "pixel_bbox": [x1, y1, x2, y2],
            "yolo_confidence": float(confidence),
            "crop": crop,
        })

    return candidates

# 전체 사진에 candidate_id를 표시
# VLM이 전체 위치와 개별 Crop을 같은 후보로 연결할 수 있게 하는 용도
def draw_candidates(
    image: Image.Image,
    candidates: list[dict],
) -> Image.Image:
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    for candidate in candidates:
        x1, y1, x2, y2 = candidate["pixel_bbox"]
        candidate_id = candidate["candidate_id"]

        draw.rectangle(
            [x1, y1, x2, y2],
            outline="red",
            width=5,
        )
        draw.text(
            (x1 + 5, y1 + 5),
            f"candidate #{candidate_id}",
            fill="red",
            stroke_width=2,
            stroke_fill="white",
        )
    return annotated

def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    1. Vision Agent
    TODO: GPT-4o Vision API를 호출하여 이미지에서 BBox 추출 및 결함(Mint) 여부를 판단하세요.
    - 핵심: 사진 촬영 거리/구도에 영향을 받지 않도록, 전체 책 면적 대비 결함의 '상대 비율(Relative Ratio)'을 추출해야 합니다.
    - 입력: state["messages"] 내의 이미지 URL
    - 출력: is_mint (bool), defects (list of relative ratios)
    """
        # 구현 흐름:
    # 1. YOLO가 결함 의심 영역의 BBox를 생성한
    # 2. 전체 사진과 후보별 Crop을 VLM에 전달
    # 3. VLM은 새 좌표를 만들지 않고 YOLO 후보를 승인하거나 거절
    # 4. 승인된 후보만 결함 종류·위치·면적 비율과 함께 State에 저장
    #
    # 방어 로직:
    # - VLM 구조화 출력으로 허용되지 않은 결함 코드를 차단
    # - candidate_id 누락·중복·추가 결과를 오류 처리
    # - 낮은 신뢰도와 YOLO 미탐 의심 결과를 HITL로 보냄
    # - Vision 재실행 시 이전 UBCI와 최종 보고서 값을 초기화

    print("[Agent] Vision Agent 실행...")

    image_paths = state.get("image_paths") or []

    raw_revision_count = state.get("revision_count", 0)
    revision_count = (
        raw_revision_count
        if type(raw_revision_count) is int
        and raw_revision_count >= 0
        else 0
    )

    # 관리자가 새 사진으로 재검수를 요청하면 재시도 횟수를 초기화
    # 초기화하지 않으면 최대 재시도 이후 다시 Vision을 통과해도 HITL로 돌아감
    if state.get("human_feedback") == "RE_CHECK":
        revision_count = 0

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

    def failure_result(message: str):
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
                AIMessage(
                    content=f"[Vision Agent] 실패 - {message}"
                )
            ],
        }

    if not image_paths:
        return failure_result(
            "검수할 image_paths가 없습니다."
        )

    prompt = """
당신은 중고 도서 결함 후보 검증 Agent입니다.

YOLO가 만든 후보 BBox만 검증합니다.
새로운 BBox나 좌표를 만들지 마세요.

첫 번째 이미지는 후보 번호가 표시된 전체 사진입니다.
그다음 이미지들은 각 candidate의 확대 Crop입니다.

각 candidate_id를 정확히 한 번씩 판정하세요.

실제 결함으로 인정:
- 찢어짐, 눌림, 구겨짐
- 실제 긁힘이나 마모
- 얼룩, 필기, 도장
- 제본 벌어짐
- 수침 변색이나 뒤틀림

반드시 거절:
- 책상, 케이블, 다른 책 등 배경 물체
- 표지의 인쇄 글자, 그림, 로고, 스티커 디자인
- 조명 반사, 그림자, 유광 광택
- 물리적 손상이 명확하지 않은 색상 차이

is_defect=false이면:
- type=null
- location=null
- ratio=0.0
- reject_reason을 반드시 지정

is_defect=true이면:
- type과 location 지정
- reject_reason=null
- ratio는 보이는 책 면적 대비 실제 결함 면적 비율
- YOLO BBox 전체 면적을 그대로 ratio로 사용하지 않음

missed_defect_suspected는 전체 사진에서 명확한 결함이 보이지만
어떤 YOLO 후보에도 포함되지 않은 경우에만 true입니다.
"""

    try:
        review_model = ChatOpenAI(
            model=os.getenv(
                "OPENAI_MODEL",
                "gpt-4o-mini",
            ),
            temperature=0,
            timeout=60,
            max_retries=1,
        ).with_structured_output(
            HybridVisionReview,
            method="json_schema",
        )

        final_defects = []
        confidence_values = []
        missed_defect_suspected = False

        repo_root = Path(__file__).resolve().parents[2]

        for image_index, raw_path in enumerate(image_paths):
            image_path = Path(raw_path)

            if not image_path.is_absolute():
                image_path = repo_root / image_path

            if not image_path.exists():
                raise FileNotFoundError(
                    f"이미지가 없습니다: {image_path}"
                )

            image = Image.open(
                image_path
            ).convert("RGB")

            candidates = detect_yolo_candidates(image)
            annotated = draw_candidates(
                image,
                candidates,
            )

            # VLM이 전체 이미지의 후보 번호와 Crop을 연결할 수 있도록
            # candidate_id와 정규화된 BBox 정보를 함께 전달
            candidate_metadata = [
                {
                    "candidate_id": candidate["candidate_id"],
                    "bbox": candidate["bbox"],
                }
                for candidate in candidates
            ]

            content = [
                {
                    "type": "text",
                    "text": (
                        "다음 YOLO 후보를 검증하세요.\n"
                        + json.dumps(
                            candidate_metadata,
                            ensure_ascii=False,
                        )
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_to_data_url(annotated),
                        "detail": "high",
                    },
                },
            ]

            for candidate in candidates:
                content.extend([
                    {
                        "type": "text",
                        "text": (
                            "다음 Crop의 candidate_id는 "
                            f"{candidate['candidate_id']}입니다."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_to_data_url(
                                candidate["crop"]
                            ),
                            "detail": "high",
                        },
                    },
                ])

            review = review_model.invoke([
                ("system", prompt),
                HumanMessage(content=content),
            ])

            expected_ids = {
                candidate["candidate_id"]
                for candidate in candidates
            }
            returned_ids = [
                item.candidate_id
                for item in review.reviews
            ]

            if (
                len(returned_ids) != len(set(returned_ids))
                or set(returned_ids) != expected_ids
            ):
                raise ValueError(
                    "VLM이 후보 ID를 누락하거나 추가했습니다."
                )

            if not review.image_quality_ok:
                return failure_result(
                    f"{image_index}번 이미지 판독 불가"
                )

            confidence_values.append(
                review.vision_confidence
            )

            missed_defect_suspected = (
                missed_defect_suspected
                or review.missed_defect_suspected
            )

            candidates_by_id = {
                candidate["candidate_id"]: candidate
                for candidate in candidates
            }

            for item in review.reviews:
                if not item.is_defect:
                    continue

                if item.type is None or item.location is None:
                    raise ValueError(
                        "승인된 결함 정보가 불완전합니다."
                    )

                candidate = candidates_by_id[
                    item.candidate_id
                ]

                defect = DefectOutput(
                    type=item.type,
                    location=item.location,
                    bbox=candidate["bbox"],
                    ratio=item.ratio,
                    confidence=item.confidence,
                    image_index=image_index,
                    text_overlap=item.text_overlap,
                    morphology_severe=(
                        item.morphology_severe
                    ),
                ).model_dump()

                # 테스트와 로그 확인을 위한 값
                defect["candidate_id"] = (
                    item.candidate_id
                )
                defect["yolo_confidence"] = (
                    candidate["yolo_confidence"]
                )

                final_defects.append(defect)
                confidence_values.append(
                    item.confidence
                )
        # 전체 평균이 아니라 가장 낮은 신뢰도를 대표값으로 사용
        # 확실한 후보 여러 개가 불확실한 후보 하나를 평균으로 감추는 것을 방지
        vision_confidence = (
            min(confidence_values)
            if confidence_values
            else 0.0
        )

    except Exception as error:
        print(
            "[Agent] Hybrid Vision 실패:",
            type(error).__name__,
            str(error),
        )
        return failure_result(
            "YOLO 탐지 또는 VLM 검증에 실패했습니다."
        )

    reason_code = None
    repair_directive = None

    if (
        missed_defect_suspected
        or vision_confidence < MIN_VISION_CONFIDENCE
    ):
        reason_code = "VISION_LOW_CONFIDENCE"
        repair_directive = (
            "YOLO 미탐 가능성 또는 낮은 VLM 신뢰도를 "
            "관리자가 확인해야 합니다."
        )
        revision_count += 1

    is_mint = (
        not final_defects
        and not missed_defect_suspected
    )

    print(
        "[Agent] YOLO 후보 검증 완료:",
        f"승인 결함 {len(final_defects)}개",
    )

    return {
        **downstream_reset,
        "is_mint": is_mint,
        "defects": final_defects,
        "image_quality_ok": True,
        "vision_confidence": vision_confidence,
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "messages": [
            AIMessage(
                content=(
                    "[Vision Agent] YOLO→VLM 검증 완료 - "
                    f"승인 결함 {len(final_defects)}개"
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
    defects: list[dict],
) -> tuple[float, list[dict], bool]:
    """
    동일 결함의 면적을 합산하여 다음 값을 반환합니다.

    1. UBCI 점수
    2. 결함별 감점 내역
    3. 치명 결함 존재 여부
    """
    # Python의 짝수 반올림을 피하고 일반적인 0.5 올림을 사용
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

        # 감점 결과는 소수점 첫째 자리까지 기록
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
