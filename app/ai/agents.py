import base64
import json
import os
import re

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ultralytics import YOLO

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
    "PAGE_FOLD",
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
    bbox: list[NormalizedValue] = Field(
        min_length=4,
        max_length=4,
    )
    ratio: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    image_index: int = Field(ge=0)
    text_overlap: bool = False
    morphology_severe: bool = False

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
    "MODEL_CLASS_CONFLICT",
]

ReviewDecision = Literal[
    "CONFIRMED",
    "REJECTED",
    "UNCERTAIN",
]


class CandidateReview(BaseModel):
    """
    VLM은 YOLO 후보를 확인할 뿐이다.

    새로운 BBox, MINT, UBCI 점수,
    등급, Reason Code를 만들 수 없다.
    """

    model_config = ConfigDict(strict=True)

    candidate_id: int = Field(ge=0)
    decision: ReviewDecision

    confirmed_type: DefectCode | None = None
    location: DefectLocation | None = None

    ratio: float = Field(ge=0, le=100)
    review_confidence: float = Field(ge=0, le=1)

    reject_reason: RejectReason | None = None
    text_overlap: bool = False
    morphology_severe: bool = False

    @model_validator(mode="after")
    def normalize_review(self):
        # CONFIRMED 출력의 필수값 검증
        confirmed_is_valid = (
            self.confirmed_type is not None
            and self.location is not None
            and self.reject_reason is None
            and self.ratio > 0
        )

        if (
            self.decision == "CONFIRMED"
            and confirmed_is_valid
        ):
            return self

        # 불완전한 CONFIRMED 출력의 HITL 전환
        if self.decision == "CONFIRMED":
            self.decision = "UNCERTAIN"

        # REJECTED·UNCERTAIN 출력 정규화
        self.confirmed_type = None
        self.location = None
        self.ratio = 0.0
        self.reject_reason = (
            self.reject_reason
            or "INSUFFICIENT_EVIDENCE"
        )

        return self


class HybridVisionReview(BaseModel):
    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    reviews: list[CandidateReview]
    missed_defect_suspected: bool
    review_confidence: float = Field(ge=0, le=1)
YOLO_IMAGE_SIZE = int(
    os.getenv("YOLO_IMAGE_SIZE", "960")
)

YOLO_MAX_PER_MODEL = int(
    os.getenv(
        "YOLO_MAX_CANDIDATES_PER_MODEL",
        "30",
    )
)

YOLO_MAX_ENSEMBLE_CANDIDATES = int(
    os.getenv(
        "YOLO_MAX_ENSEMBLE_CANDIDATES",
        "15",
    )
)

YOLO_ENSEMBLE_IOU = float(
    os.getenv("YOLO_ENSEMBLE_IOU", "0.55")
)


@dataclass(frozen=True)
class YoloModelSpec:
    name: str
    env_name: str
    default_path: str
    role: str
    confidence: float
    class_mapping: dict[str, str]


YOLO_MODEL_SPECS = (
    YoloModelSpec(
        name="general_binary",
        env_name="YOLO_GENERAL_MODEL_PATH",
        default_path=(
            "models/general_binary_1559_best.pt"
        ),
        role="GENERAL_RECALL",
        confidence=0.15,
        class_mapping={
            "wornout": "OTHER_VISIBLE_DAMAGE",
            "ripped": "COVER_TEAR",
        },
    ),
    YoloModelSpec(
        name="physical4",
        env_name="YOLO_PHYSICAL_MODEL_PATH",
        default_path="models/physical4_best.pt",
        role="PHYSICAL_SPECIALIST",
        confidence=0.20,
        class_mapping={
            "cover_tear": "COVER_TEAR",
            "edge_wear": "EDGE_WEAR",
            "general_stain": "GENERAL_STAIN",
            "page_fold": "PAGE_FOLD",
        },
    ),
    YoloModelSpec(
        name="doodle",
        env_name="YOLO_DOODLE_MODEL_PATH",
        default_path="models/doodle_best.pt",
        role="DOODLE_SPECIALIST",
        confidence=0.20,
        class_mapping={
        # 기존 Doodle 모델의 실제 클래스명
        "item": "WRITING",
        # 클래스명 변경 모델과의 호환
        "doodle_scribble": "WRITING",
        },
    ),
)

MODEL_PRIORITY = {
    "general_binary": 1,
    "physical4": 2,
    "doodle": 3,
}


def trace_event(
    event: str,
    payload: dict,
) -> None:
    """에이전트의 입력·출력을 한 줄 JSON으로 표시한다."""

    print(
        "[AI_TRACE]",
        json.dumps(
            {
                "event": event,
                **payload,
            },
            ensure_ascii=False,
            default=str,
        ),
    )


def normalize_model_class(value: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        value.strip().lower(),
    )
    return normalized.strip("_")


def resolve_model_path(
    configured_path: str,
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    path = Path(configured_path)

    if path.is_absolute():
        return path

    return repo_root / path


@lru_cache(maxsize=1)
def get_yolo_models() -> dict[str, dict]:
    """활성화된 YOLO 모델을 최초 한 번만 불러온다."""

    enabled_names = {
        item.strip()
        for item in os.getenv(
            "YOLO_ENABLED_MODELS",
            "general_binary,physical4,doodle",
        ).split(",")
        if item.strip()
    }

    known_names = {
        spec.name
        for spec in YOLO_MODEL_SPECS
    }

    unknown_names = enabled_names - known_names

    if unknown_names:
        raise ValueError(
            "알 수 없는 YOLO 모델: "
            + ", ".join(sorted(unknown_names))
        )

    loaded: dict[str, dict] = {}
    missing: list[str] = []

    for spec in YOLO_MODEL_SPECS:
        if spec.name not in enabled_names:
            continue

        model_path = resolve_model_path(
            os.getenv(
                spec.env_name,
                spec.default_path,
            )
        )

        if not model_path.is_file():
            missing.append(
                f"{spec.name}={model_path}"
            )
            continue

        model = YOLO(str(model_path))

        names = {
            int(key): str(value)
            for key, value in model.names.items()
        }

        loaded[spec.name] = {
            "spec": spec,
            "model": model,
            "path": str(model_path),
            "classes": names,
        }

    if missing:
        raise FileNotFoundError(
            "YOLO 모델 파일이 없습니다: "
            + " | ".join(missing)
        )

    if not loaded:
        raise RuntimeError(
            "활성화된 YOLO 모델이 없습니다."
        )

    trace_event(
        "YOLO_MODELS_LOADED",
        {
            "models": [
                {
                    "name": name,
                    "path": item["path"],
                    "role": item["spec"].role,
                    "classes": item["classes"],
                }
                for name, item in loaded.items()
            ]
        },
    )

    return loaded


def get_yolo_model_manifest() -> list[dict]:
    return [
        {
            "name": name,
            "path": item["path"],
            "role": item["spec"].role,
            "classes": item["classes"],
        }
        for name, item in get_yolo_models().items()
    ]


def image_to_data_url(
    image: Image.Image,
) -> str:
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


def calculate_bbox_iou(
    first: list[float],
    second: list[float],
) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])

    intersection = (
        max(0.0, x2 - x1)
        * max(0.0, y2 - y1)
    )

    first_area = (
        max(0.0, first[2] - first[0])
        * max(0.0, first[3] - first[1])
    )

    second_area = (
        max(0.0, second[2] - second[0])
        * max(0.0, second[3] - second[1])
    )

    union = (
        first_area
        + second_area
        - intersection
    )

    if union <= 0:
        return 0.0

    return intersection / union


def merge_model_detections(
    detections: list[dict],
) -> list[list[dict]]:
    clusters: list[list[dict]] = []

    sorted_detections = sorted(
        detections,
        key=lambda item: item["confidence"],
        reverse=True,
    )

    for detection in sorted_detections:
        matching_cluster = next(
            (
                cluster
                for cluster in clusters
                if any(
                    calculate_bbox_iou(
                        detection["bbox"],
                        existing["bbox"],
                    )
                    >= YOLO_ENSEMBLE_IOU
                    for existing in cluster
                )
            ),
            None,
        )

        if matching_cluster is None:
            clusters.append([detection])
        else:
            matching_cluster.append(detection)

    return clusters


def weighted_bbox(
    detections: list[dict],
) -> list[float]:
    weights = [
        max(item["confidence"], 0.000001)
        for item in detections
    ]

    total = sum(weights)

    return [
        round(
            sum(
                item["bbox"][index] * weight
                for item, weight in zip(
                    detections,
                    weights,
                )
            ) / total,
            6,
        )
        for index in range(4)
    ]


def choose_proposed_type(
    detections: list[dict],
) -> str:
    selected = max(
        detections,
        key=lambda item: (
            item["mapped_type"]
            != "OTHER_VISIBLE_DAMAGE",
            MODEL_PRIORITY.get(
                item["source_model"],
                0,
            ),
            item["confidence"],
        ),
    )

    return selected["mapped_type"]


def detect_yolo_candidates(
    image: Image.Image,
    image_index: int,
) -> tuple[list[dict], list[dict]]:
    """
    모든 YOLO 모델을 실행하고
    겹치는 BBox를 후보 하나로 합친다.
    """

    width, height = image.size
    raw_detections: list[dict] = []

    for model_name, item in get_yolo_models().items():
        spec: YoloModelSpec = item["spec"]
        model: YOLO = item["model"]

        result = model.predict(
            source=image,
            conf=float(
                os.getenv(
                    (
                        f"YOLO_"
                        f"{model_name.upper()}"
                        f"_CONFIDENCE"
                    ),
                    str(spec.confidence),
                )
            ),
            iou=float(
                os.getenv(
                    "YOLO_MODEL_NMS_IOU",
                    "0.50",
                )
            ),
            imgsz=YOLO_IMAGE_SIZE,
            max_det=YOLO_MAX_PER_MODEL,
            device=os.getenv(
                "YOLO_DEVICE",
                "cpu",
            ),
            verbose=False,
        )[0]

        if result.boxes is None:
            continue

        boxes = result.boxes.xyxy.cpu().tolist()
        confidences = (
            result.boxes.conf.cpu().tolist()
        )
        class_ids = (
            result.boxes.cls.cpu().tolist()
        )

        for (
            box,
            confidence,
            raw_class_id,
        ) in zip(
            boxes,
            confidences,
            class_ids,
        ):
            class_id = int(raw_class_id)
            source_class = str(
                model.names[class_id]
            )

            normalized_class = (
                normalize_model_class(
                    source_class
                )
            )

            mapped_type = (
                spec.class_mapping.get(
                    normalized_class,
                    "OTHER_VISIBLE_DAMAGE",
                )
            )

            x1, y1, x2, y2 = box

            x1 = max(
                0,
                min(int(x1), width - 1),
            )
            y1 = max(
                0,
                min(int(y1), height - 1),
            )
            x2 = max(
                x1 + 1,
                min(int(x2), width),
            )
            y2 = max(
                y1 + 1,
                min(int(y2), height),
            )

            raw_detections.append({
                "image_index": image_index,
                "source_model": model_name,
                "source_role": spec.role,
                "source_class": source_class,
                "source_class_id": class_id,
                "mapped_type": mapped_type,
                "confidence": round(
                    float(confidence),
                    6,
                ),
                "bbox": [
                    x1 / width,
                    y1 / height,
                    x2 / width,
                    y2 / height,
                ],
                "pixel_bbox": [
                    x1,
                    y1,
                    x2,
                    y2,
                ],
            })

    candidates: list[dict] = []

    clusters = merge_model_detections(
        raw_detections
    )

    for candidate_id, cluster in enumerate(
        clusters
    ):
        bbox = weighted_bbox(cluster)

        x1 = max(
            0,
            min(
                int(bbox[0] * width),
                width - 1,
            ),
        )
        y1 = max(
            0,
            min(
                int(bbox[1] * height),
                height - 1,
            ),
        )
        x2 = max(
            x1 + 1,
            min(
                int(bbox[2] * width),
                width,
            ),
        )
        y2 = max(
            y1 + 1,
            min(
                int(bbox[3] * height),
                height,
            ),
        )

        padding_x = int(
            (x2 - x1) * 0.15
        )
        padding_y = int(
            (y2 - y1) * 0.15
        )

        crop = image.crop((
            max(0, x1 - padding_x),
            max(0, y1 - padding_y),
            min(width, x2 + padding_x),
            min(height, y2 + padding_y),
        ))

        specialist_types = {
            detection["mapped_type"]
            for detection in cluster
            if detection["mapped_type"]
            != "OTHER_VISIBLE_DAMAGE"
        }

        candidates.append({
            "candidate_id": candidate_id,
            "image_index": image_index,
            "bbox": bbox,
            "pixel_bbox": [
                x1,
                y1,
                x2,
                y2,
            ],
            "proposed_type": (
                choose_proposed_type(cluster)
            ),
            "yolo_confidence": max(
                detection["confidence"]
                for detection in cluster
            ),
            "ensemble_confidence": round(
                sum(
                    detection["confidence"]
                    for detection in cluster
                ) / len(cluster),
                6,
            ),
            "source_models": sorted({
                detection["source_model"]
                for detection in cluster
            }),
            "source_predictions": cluster,
            "class_conflict": (
                len(specialist_types) > 1
            ),
            "crop": crop,
        })

    candidates.sort(
        key=lambda candidate: (
            len(candidate["source_models"]),
            candidate["yolo_confidence"],
        ),
        reverse=True,
    )

    candidates = candidates[
        :YOLO_MAX_ENSEMBLE_CANDIDATES
    ]

    for candidate_id, candidate in enumerate(
        candidates
    ):
        candidate["candidate_id"] = (
            candidate_id
        )

    trace_event(
        "YOLO_ENSEMBLE_COMPLETED",
        {
            "image_index": image_index,
            "raw_detections": raw_detections,
            "ensemble_candidates": [
                {
                    key: value
                    for key, value
                    in candidate.items()
                    if key != "crop"
                }
                for candidate in candidates
            ],
        },
    )

    return raw_detections, candidates

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

def state_safe_candidate(
    candidate: dict,
) -> dict:
    """
    PIL Crop을 제외하고
    JSON과 LangGraph State에 저장할 수 있게 만든다.
    """

    return {
        key: value
        for key, value in candidate.items()
        if key != "crop"
    }

def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    1. Vision Agent
    TODO: GPT-4o Vision API를 호출하여 이미지에서 BBox 추출 및 결함(Mint) 여부를 판단하세요.
    - 핵심: 사진 촬영 거리/구도에 영향을 받지 않도록, 전체 책 면적 대비 결함의 '상대 비율(Relative Ratio)'을 추출해야 합니다.
    - 입력: state["messages"] 내의 이미지 URL
    - 출력: is_mint (bool), defects (list of relative ratios)
    """

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
        # 아래 값은 Vision이 아니라
        # Policy가 결정한다.
        "is_mint": None,
        "ubci_score": None,
        "predicted_grade": None,
        "score_breakdown": None,
        "fatal_defect_detected": None,
        "grade_reason_code": None,
        "rule_reference": None,
        "policy_confidence": None,
        "overall_confidence": None,
        "human_feedback": None,
        "primary_reason_code": (
            state.get("primary_reason_code")
            if state.get("human_feedback")
            == "RE_CHECK"
            else None
        ),
        "target_grade": None,
        "final_grade": None,
        "final_report": None,
    }

    def failure_result(
        message: str,
    ) -> WMSInspectionState:
        result = {
            **downstream_reset,
            "yolo_model_manifest": None,
            "raw_yolo_detections": [],
            "ensemble_candidates": [],
            "reviewed_candidates": [],
            "rejected_candidates": [],
            "uncertain_candidates": [],
            "defects": None,
            "image_quality_ok": False,
            "vision_confidence": None,
            "vision_status": "FAILED",
            "vision_reason_code": (
                "QUALITY_ERROR"
            ),
            "reason_code": None,
            "repair_directive": message,
            "revision_count": (
                revision_count + 1
            ),
            "messages": [
                AIMessage(
                    content=(
                        "[Vision Agent] 실행 실패 - "
                        f"{message}"
                    )
                )
            ],
        }

        trace_event(
            "VISION_OUTPUT",
            {
                "vision_status": "FAILED",
                "vision_reason_code": (
                    "QUALITY_ERROR"
                ),
                "repair_directive": message,
                "revision_count": (
                    result["revision_count"]
                ),
            },
        )

        return result

    if not image_paths:
        return failure_result(
            "검사할 image_paths가 없습니다."
        )

    prompt = """
당신은 중고 도서 검수 시스템의 2차 확인자입니다.

역할과 금지사항:
1. YOLO 앙상블이 만든 candidate_id와 BBox만 검토합니다.
2. 새로운 candidate, BBox, 좌표를 절대 만들지 않습니다.
3. is_mint, UBCI 점수, 등급, reason_code를 결정하지 않습니다.
4. 각 candidate_id를 정확히 한 번만 반환합니다.

입력 이해:
- 첫 이미지는 후보 번호가 표시된 전체 사진입니다.
- 다음 이미지는 각 후보의 확대 Crop입니다.
- proposed_type은 YOLO가 제안한 결함 유형입니다.
- source_models와 source_predictions는 모델별 판단입니다.
- class_conflict=true이면 전문 모델들의 유형이 충돌한 것입니다.

decision 규칙:
- CONFIRMED: 물리적인 결함이 분명할 때
- REJECTED: 표지 디자인, 인쇄, 그림자, 빛 반사, 배경 등 오탐일 때
- UNCERTAIN: 흐림, 가림, 증거 부족, 모델 충돌로 확정할 수 없을 때

결함 유형 규칙:
- proposed_type이 OTHER_VISIBLE_DAMAGE가 아니면
  CONFIRMED의 confirmed_type을 proposed_type과 같게 유지합니다.
- OTHER_VISIBLE_DAMAGE인 일반 모델 후보만 근거가 분명한 경우
  구체적인 허용 결함 유형으로 세분화합니다.
- 전문 모델의 proposed_type에 동의하지 않으면
  다른 유형으로 변경하지 말고 UNCERTAIN을 반환합니다.

필드 규칙:
- CONFIRMED:
  confirmed_type과 location 필수
  reject_reason=null
  ratio는 책 면적 대비 실제 결함 면적 비율
- REJECTED 또는 UNCERTAIN:
  confirmed_type=null
  location=null
  ratio=0.0
  reject_reason 필수
- missed_defect_suspected는 전체 사진에 명확한 결함이 있지만
  어느 YOLO 후보에도 들어 있지 않을 때만 true입니다.
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

        model_manifest = (
            get_yolo_model_manifest()
        )

        all_raw_detections: list[dict] = []
        all_ensemble_candidates: list[dict] = []
        reviewed_candidates: list[dict] = []
        rejected_candidates: list[dict] = []
        uncertain_candidates: list[dict] = []
        final_defects: list[dict] = []
        confidence_values: list[float] = []

        all_image_quality_ok = True
        missed_defect_suspected = False

        repo_root = (
            Path(__file__)
            .resolve()
            .parents[2]
        )

        for (
            image_index,
            raw_path,
        ) in enumerate(image_paths):
            image_path = Path(raw_path)

            if not image_path.is_absolute():
                image_path = (
                    repo_root / image_path
                )

            if not image_path.exists():
                raise FileNotFoundError(
                    "이미지가 없습니다: "
                    f"{image_path}"
                )

            image = Image.open(
                image_path
            ).convert("RGB")

            (
                raw_detections,
                candidates,
            ) = detect_yolo_candidates(
                image,
                image_index,
            )

            all_raw_detections.extend(
                raw_detections
            )

            all_ensemble_candidates.extend([
                state_safe_candidate(
                    candidate
                )
                for candidate in candidates
            ])

            annotated = draw_candidates(
                image,
                candidates,
            )

            candidate_metadata = [
                {
                    "candidate_id": (
                        candidate[
                            "candidate_id"
                        ]
                    ),
                    "bbox": candidate["bbox"],
                    "proposed_type": (
                        candidate[
                            "proposed_type"
                        ]
                    ),
                    "yolo_confidence": (
                        candidate[
                            "yolo_confidence"
                        ]
                    ),
                    "ensemble_confidence": (
                        candidate[
                            "ensemble_confidence"
                        ]
                    ),
                    "source_models": (
                        candidate[
                            "source_models"
                        ]
                    ),
                    "source_predictions": (
                        candidate[
                            "source_predictions"
                        ]
                    ),
                    "class_conflict": (
                        candidate[
                            "class_conflict"
                        ]
                    ),
                }
                for candidate in candidates
            ]

            content = [
                {
                    "type": "text",
                    "text": (
                        "다음 YOLO 앙상블 후보를 "
                        "규칙대로 검토하세요.\n"
                        + json.dumps(
                            candidate_metadata,
                            ensure_ascii=False,
                        )
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            image_to_data_url(
                                annotated
                            )
                        ),
                        "detail": "high",
                    },
                },
            ]

            for candidate in candidates:
                content.extend([
                    {
                        "type": "text",
                        "text": (
                            "다음 확대 이미지의 "
                            "candidate_id는 "
                            f"{candidate['candidate_id']}"
                            "입니다. BBox는 변경할 수 "
                            "없습니다."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": (
                                image_to_data_url(
                                    candidate[
                                        "crop"
                                    ]
                                )
                            ),
                            "detail": "high",
                        },
                    },
                ])

            review = review_model.invoke([
                ("system", prompt),
                HumanMessage(
                    content=content
                ),
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
                len(returned_ids)
                != len(set(returned_ids))
                or set(returned_ids)
                != expected_ids
            ):
                raise ValueError(
                    "VLM이 후보 ID를 "
                    "누락·중복·추가했습니다."
                )

            all_image_quality_ok = (
                all_image_quality_ok
                and review.image_quality_ok
            )

            confidence_values.append(
                review.review_confidence
            )

            missed_defect_suspected = (
                missed_defect_suspected
                or review.missed_defect_suspected
            )

            candidates_by_id = {
                candidate["candidate_id"]:
                    candidate
                for candidate in candidates
            }

            for item in review.reviews:
                candidate = candidates_by_id[
                    item.candidate_id
                ]

                safe_candidate = (
                    state_safe_candidate(
                        candidate
                    )
                )

                review_payload = (
                    item.model_dump()
                )

                review_record = {
                    **safe_candidate,
                    "vlm_review": (
                        review_payload
                    ),
                }

                reviewed_candidates.append(
                    review_record
                )

                confidence_values.append(
                    item.review_confidence
                )

                if (
                    item.decision
                    == "REJECTED"
                ):
                    rejected_candidates.append(
                        review_record
                    )
                    continue

                if (
                    item.decision
                    == "UNCERTAIN"
                ):
                    uncertain_candidates.append(
                        review_record
                    )
                    continue

                proposed_type = candidate[
                    "proposed_type"
                ]

                if (
                    proposed_type
                    != "OTHER_VISIBLE_DAMAGE"
                    and item.confirmed_type
                    != proposed_type
                ):
                    uncertain_payload = {
                        **review_payload,
                        "decision": "UNCERTAIN",
                        "confirmed_type": None,
                        "location": None,
                        "ratio": 0.0,
                        "reject_reason": (
                            "MODEL_CLASS_CONFLICT"
                        ),
                    }

                    uncertain_record = {
                        **safe_candidate,
                        "vlm_review": uncertain_payload,
                    }

                    # 전체 검토 목록과 보류 목록의 상태 통일
                    reviewed_candidates[-1] = (
                        uncertain_record
                    )
                    uncertain_candidates.append(
                        uncertain_record
                    )
                    continue

                defect = DefectOutput(
                    type=item.confirmed_type,
                    location=item.location,
                    bbox=candidate["bbox"],
                    ratio=item.ratio,
                    confidence=(
                        item.review_confidence
                    ),
                    image_index=image_index,
                    text_overlap=(
                        item.text_overlap
                    ),
                    morphology_severe=(
                        item.morphology_severe
                    ),
                ).model_dump()

                defect.update({
                    "candidate_id": (
                        item.candidate_id
                    ),
                    "proposed_type": (
                        proposed_type
                    ),
                    "yolo_confidence": (
                        candidate[
                            "yolo_confidence"
                        ]
                    ),
                    "ensemble_confidence": (
                        candidate[
                            "ensemble_confidence"
                        ]
                    ),
                    "vlm_confidence": (
                        item.review_confidence
                    ),
                    "source_models": (
                        candidate[
                            "source_models"
                        ]
                    ),
                    "source_predictions": (
                        candidate[
                            "source_predictions"
                        ]
                    ),
                    "class_conflict": (
                        candidate[
                            "class_conflict"
                        ]
                    ),
                })

                final_defects.append(
                    defect
                )

        vision_confidence = (
            min(confidence_values)
            if confidence_values
            else 1.0
        )

    except Exception as error:
        print(
            "[Agent] Hybrid Vision 실패:",
            type(error).__name__,
            str(error),
        )

        return failure_result(
            "YOLO 앙상블 또는 VLM 검토 실패: "
            f"{type(error).__name__}: {error}"
        )

    vision_status = "COMPLETED"
    vision_reason_code = None
    repair_directive = None

    if (
        not all_image_quality_ok
        or vision_confidence
        < MIN_VISION_CONFIDENCE
    ):
        vision_status = "REVIEW_REQUIRED"
        vision_reason_code = (
            "VISION_LOW_CONFIDENCE"
        )
        repair_directive = (
            "사진 품질 또는 판단 신뢰도가 낮아 "
            "관리자 확인이나 재촬영이 필요합니다."
        )
        revision_count += 1

    elif (
        missed_defect_suspected
        or uncertain_candidates
    ):
        vision_status = "REVIEW_REQUIRED"
        vision_reason_code = (
            "VISION_UNCLASSIFIED_DEFECT"
        )
        repair_directive = (
            "YOLO 미탐 가능성, 클래스 충돌 또는 "
            "VLM 보류 후보를 관리자가 확인해야 합니다."
        )
        revision_count += 1

    result = {
        **downstream_reset,
        # 승인된 결함이 없는 완료 결과의 MINT 판정
        "is_mint": (
            not final_defects
            if vision_status == "COMPLETED"
            else None
        ),
        "yolo_model_manifest": (
            model_manifest
        ),
        "raw_yolo_detections": (
            all_raw_detections
        ),
        "ensemble_candidates": (
            all_ensemble_candidates
        ),
        "reviewed_candidates": (
            reviewed_candidates
        ),
        "rejected_candidates": (
            rejected_candidates
        ),
        "uncertain_candidates": (
            uncertain_candidates
        ),
        "defects": final_defects,
        "image_quality_ok": (
            all_image_quality_ok
        ),
        "vision_confidence": (
            vision_confidence
        ),
        "vision_status": vision_status,
        "vision_reason_code": (
            vision_reason_code
        ),
        "reason_code": None,
        "repair_directive": (
            repair_directive
        ),
        "revision_count": (
            revision_count
        ),
        "messages": [
            AIMessage(
                content=(
                    "[Vision Agent] 완료 - "
                    f"상태={vision_status}, "
                    f"승인={len(final_defects)}, "
                    "거절="
                    f"{len(rejected_candidates)}, "
                    "보류="
                    f"{len(uncertain_candidates)}"
                )
            )
        ],
    }

    trace_event(
        "VISION_OUTPUT",
        {
            "vision_status": (
                vision_status
            ),
            "vision_reason_code": (
                vision_reason_code
            ),
            "image_quality_ok": (
                all_image_quality_ok
            ),
            "vision_confidence": (
                vision_confidence
            ),
            "model_manifest": (
                model_manifest
            ),
            "raw_detection_count": (
                len(all_raw_detections)
            ),
            "ensemble_candidate_count": (
                len(
                    all_ensemble_candidates
                )
            ),
            "confirmed_defects": (
                final_defects
            ),
            "rejected_candidates": (
                rejected_candidates
            ),
            "uncertain_candidates": (
                uncertain_candidates
            ),
            "repair_directive": (
                repair_directive
            ),
            "revision_count": (
                revision_count
            ),
        },
    )

    return result


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

HITL_REQUIRED_DEFECTS = {
    "WRITING",
    "HIGHLIGHTING",
    "BARCODE_DAMAGE",
    "OTHER_VISIBLE_DAMAGE",

    # PAGE_FOLD는 모델이 탐지할 수 있지만
    # UBCI v2 감점표가 아직 없으므로
    # 임의 감점하지 않고 관리자에게 전달
    "PAGE_FOLD",
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
            "ubci_score는 0~100 범위의 숫자여야 합니다."
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

    defects = state.get("defects")
    vision_status = state.get(
        "vision_status"
    )

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

    try:
        if vision_status != "COMPLETED":
            raise ValueError(
                "완료된 Vision 결과만 "
                "Policy가 계산할 수 있습니다."
            )

        if type(defects) is not list:
            raise ValueError(
                "defects는 list여야 합니다."
            )

        if any(
            type(defect) is not dict
            for defect in defects
        ):
            raise ValueError(
                "defects의 각 항목은 dict여야 합니다."
            )

        # 결함이 없을 때 Policy가 MINT 확정
        if not defects:
            result = {
                "is_mint": True,
                "ubci_score": 100.0,
                "predicted_grade": "S",
                "score_breakdown": [],
                "fatal_defect_detected": False,
                "grade_reason_code": (
                    "NO_VISIBLE_DEFECT"
                ),
                "rule_reference": (
                    POLICY_VERSION
                ),
                "policy_confidence": 1.0,
                "reason_code": None,
                "repair_directive": None,
            }

        else:
            manual_types = sorted(
                {
                    defect.get("type")
                    for defect in defects
                }
                & HITL_REQUIRED_DEFECTS
            )

            if manual_types:
                result = {
                    "is_mint": False,
                    "ubci_score": None,
                    "predicted_grade": None,
                    "score_breakdown": None,
                    "fatal_defect_detected": None,
                    "grade_reason_code": (
                        manual_types[0]
                    ),
                    "rule_reference": (
                        POLICY_VERSION
                    ),
                    "policy_confidence": None,
                    "reason_code": (
                        "POLICY_REQUIRES_HITL"
                    ),
                    "repair_directive": (
                        "UBCI 감점 규칙 또는 사람 확인이 "
                        "필요한 결함: "
                        + ", ".join(manual_types)
                    ),
                }

            else:
                (
                    ubci_score,
                    score_breakdown,
                    fatal_defect_detected,
                ) = calculate_ubci_score(
                    defects
                )

                predicted_grade = (
                    calculate_ubci_grade(
                        ubci_score,
                        fatal_defect_detected,
                    )
                )

                if fatal_defect_detected:
                    grade_reason_code = next(
                        item["type"]
                        for item
                        in score_breakdown
                        if item["fatal"]
                    )
                else:
                    grade_reason_code = max(
                        score_breakdown,
                        key=lambda item: (
                            item[
                                "applied_penalty"
                            ]
                        ),
                    )["type"]

                result = {
                    "is_mint": False,

                    # DB와 동일하게 float 보장
                    "ubci_score": float(
                        ubci_score
                    ),

                    "predicted_grade": (
                        predicted_grade
                    ),
                    "score_breakdown": (
                        score_breakdown
                    ),
                    "fatal_defect_detected": (
                        fatal_defect_detected
                    ),
                    "grade_reason_code": (
                        grade_reason_code
                    ),
                    "rule_reference": (
                        POLICY_VERSION
                    ),
                    "policy_confidence": 1.0,
                    "reason_code": None,
                    "repair_directive": None,
                }

    except (TypeError, ValueError) as error:
        revision_count += 1

        result = {
            "is_mint": None,
            "ubci_score": None,
            "predicted_grade": None,
            "score_breakdown": None,
            "fatal_defect_detected": None,
            "grade_reason_code": None,
            "rule_reference": None,
            "policy_confidence": None,
            "reason_code": (
                "UBCI_POLICY_VIOLATION"
            ),
            "repair_directive": str(error),
        }

    output = {
        **result,
        "revision_count": revision_count,
        "overall_confidence": None,
        "human_feedback": None,
        "primary_reason_code": None,
        "target_grade": None,
        "final_grade": None,
        "final_report": None,
        "messages": [
            AIMessage(
                content=(
                    "[Policy Agent] 계산 결과 - "
                    f"{result['reason_code'] or '정상'}, "
                    f"MINT={result['is_mint']}, "
                    f"UBCI={result['ubci_score']}, "
                    "등급="
                    f"{result['predicted_grade']}"
                )
            )
        ],
    }

    trace_event(
        "POLICY_OUTPUT",
        {
            "is_mint": (
                output["is_mint"]
            ),
            "input_defects": defects,
            "ubci_score": (
                output["ubci_score"]
            ),
            "predicted_grade": (
                output["predicted_grade"]
            ),
            "score_breakdown": (
                output["score_breakdown"]
            ),
            "fatal_defect_detected": (
                output[
                    "fatal_defect_detected"
                ]
            ),
            "grade_reason_code": (
                output[
                    "grade_reason_code"
                ]
            ),
            "rule_reference": (
                output["rule_reference"]
            ),
            "policy_confidence": (
                output[
                    "policy_confidence"
                ]
            ),
            "reason_code": (
                output["reason_code"]
            ),
            "repair_directive": (
                output[
                    "repair_directive"
                ]
            ),
            "revision_count": (
                output["revision_count"]
            ),
        },
    )

    return output


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
