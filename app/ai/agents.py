import base64
import json
import os
import re

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ultralytics import YOLO
from .rag.critic_cases import (
    CRITIC_PROMPT_VERSION,
    evaluate_with_precedents,
)
from .rag.policy_search import (
    UBCI_POLICY_VERSION,
    search_policy_rules,
)

from .state import Grade, WMSInspectionState

POLICY_VERSION = UBCI_POLICY_VERSION

load_dotenv()

# 신뢰도가 이 값보다 낮으면 자동 처리하지 않고 재검토
MIN_VISION_CONFIDENCE = float(
    os.getenv("MIN_VISION_CONFIDENCE", "0.80")
)

MIN_POLICY_CONFIDENCE = float(
    os.getenv("MIN_POLICY_CONFIDENCE", "0.75")
)

VLM_CROP_MIN_SIDE = int(
    os.getenv("VLM_CROP_MIN_SIDE", "256")
)

VLM_CROP_CONTEXT_SCALE = float(
    os.getenv("VLM_CROP_CONTEXT_SCALE", "1.8")
)

MAX_INSPECTION_IMAGE_PIXELS = int(
    os.getenv(
        "MAX_INSPECTION_IMAGE_PIXELS",
        "40000000",
    )
)

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
    observation: str = Field(default="", max_length=500)

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
    "PRINTED_CONTENT",
    "LIGHT_OR_SHADOW",
    "INSUFFICIENT_EVIDENCE",
]

ReviewDecision = Literal[
    "CONFIRMED",
    "REJECTED",
    "UNCERTAIN",
]


class CandidateReview(BaseModel):
    """YOLO 결함 후보에 대한 VLM 검토 결과."""

    model_config = ConfigDict(strict=True)

    candidate_id: int = Field(ge=0)
    decision: ReviewDecision

    confirmed_type: DefectCode | None = None
    location: DefectLocation | None = None

    # 결함 존재 여부가 아닌 현재 판정의 확실성
    review_confidence: float = Field(ge=0, le=1)

    reject_reason: RejectReason | None = None
    text_overlap: bool = False
    morphology_severe: bool = False

    @model_validator(mode="after")
    def normalize_review(self):
        # 낮은 신뢰도의 자동 확정·자동 거절 차단
        if (
            self.review_confidence
            < MIN_VISION_CONFIDENCE
        ):
            self.decision = "UNCERTAIN"
            self.reject_reason = (
                "INSUFFICIENT_EVIDENCE"
            )

        confirmed_is_valid = (
            self.confirmed_type is not None
            and self.location is not None
            and self.reject_reason is None
        )

        if (
            self.decision == "CONFIRMED"
            and confirmed_is_valid
        ):
            return self

        if self.decision == "CONFIRMED":
            self.decision = "UNCERTAIN"

        self.confirmed_type = None
        self.location = None
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


class FullImageVisionReview(BaseModel):
    """학습셋이 없는 사진을 전체 판독한 결과."""

    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    defects: list[DefectOutput] = Field(max_length=20)
    observations: list[
        Annotated[str, Field(max_length=500)]
    ] = Field(default_factory=list, max_length=20)
    review_confidence: float = Field(ge=0, le=1)


class CombinedDefectReview(BaseModel):
    model_config = ConfigDict(strict=True)

    defect_index: int = Field(ge=0)
    decision: ReviewDecision
    review_confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=500)
    printed_content_only: bool = False


class CombinedVisionReview(BaseModel):
    """두 경로가 합류한 뒤 수행하는 독립 검증 결과."""

    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    reviews: list[CombinedDefectReview] = Field(max_length=30)
    missed_defect_suspected: bool
    review_confidence: float = Field(ge=0, le=1)

# 프론트 검수 이미지의 고정 순서
IMAGE_VIEWS = (
    "FRONT",
    "BACK",
    "INNER",
)

# 라우팅 기준은 각 촬영 유형의 학습셋 보유 여부입니다.
TRAINED_MODEL_VIEWS = frozenset({"FRONT", "BACK"})
UNTRAINED_VLM_VIEWS = frozenset({"INNER"})

# 책 전체 영역 탐지 설정
YOLO_BOOK_MODEL_PATH = os.getenv(
    "YOLO_BOOK_MODEL_PATH",
    "models/yolov8x-worldv2.pt",
)

YOLO_BOOK_PROMPT = (
    os.getenv("YOLO_BOOK_PROMPT", "book").strip()
    or "book"
)

YOLO_BOOK_CONFIDENCE = float(
    os.getenv("YOLO_BOOK_CONFIDENCE", "0.25")
)

YOLO_BOOK_IMAGE_SIZE = int(
    os.getenv("YOLO_BOOK_IMAGE_SIZE", "640")
)

YOLO_BOOK_PADDING = float(
    os.getenv("YOLO_BOOK_PADDING", "0.05")
)

# 전체 사진에서 책 영역이 차지해야 하는 최소 비율
YOLO_BOOK_MIN_AREA_RATIO = float(
    os.getenv(
        "YOLO_BOOK_MIN_AREA_RATIO",
        "0.20",
    )
)

# 결함 후보 BBox 중 책 영역과 겹쳐야 하는 최소 비율
YOLO_BOOK_CANDIDATE_MIN_COVERAGE = float(
    os.getenv(
        "YOLO_BOOK_CANDIDATE_MIN_COVERAGE",
        "0.60",
    )
)

YOLO_IMAGE_SIZE = int(
    os.getenv("YOLO_IMAGE_SIZE", "960")
)

YOLO_MAX_PER_MODEL = int(
    os.getenv(
        "YOLO_MAX_CANDIDATES_PER_MODEL",
        "10",
    )
)

YOLO_MAX_ENSEMBLE_CANDIDATES = int(
    os.getenv(
        "YOLO_MAX_ENSEMBLE_CANDIDATES",
        "6",
    )
)

YOLO_ENSEMBLE_IOU = float(
    os.getenv("YOLO_ENSEMBLE_IOU", "0.55")
)


# VLM 확정 결함의 중복 제거 기준
FINAL_DEFECT_DEDUP_IOU = float(
    os.getenv("FINAL_DEFECT_DEDUP_IOU", "0.40")
)

_NORMALIZED_SETTINGS = {
    "MIN_VISION_CONFIDENCE": MIN_VISION_CONFIDENCE,
    "MIN_POLICY_CONFIDENCE": MIN_POLICY_CONFIDENCE,
    "YOLO_BOOK_CONFIDENCE": YOLO_BOOK_CONFIDENCE,
    "YOLO_BOOK_PADDING": YOLO_BOOK_PADDING,
    "YOLO_BOOK_MIN_AREA_RATIO": YOLO_BOOK_MIN_AREA_RATIO,
    "YOLO_BOOK_CANDIDATE_MIN_COVERAGE": (
        YOLO_BOOK_CANDIDATE_MIN_COVERAGE
    ),
    "YOLO_ENSEMBLE_IOU": YOLO_ENSEMBLE_IOU,
    "FINAL_DEFECT_DEDUP_IOU": FINAL_DEFECT_DEDUP_IOU,
}

for setting_name, setting_value in _NORMALIZED_SETTINGS.items():
    if not 0.0 <= setting_value <= 1.0:
        raise ValueError(
            f"{setting_name}는 0과 1 사이여야 합니다."
        )

if not 1.0 <= VLM_CROP_CONTEXT_SCALE <= 10.0:
    raise ValueError(
        "VLM_CROP_CONTEXT_SCALE은 1과 10 사이여야 합니다."
    )

_POSITIVE_BOUNDED_SETTINGS = {
    "VLM_CROP_MIN_SIDE": (VLM_CROP_MIN_SIDE, 4096),
    "MAX_INSPECTION_IMAGE_PIXELS": (
        MAX_INSPECTION_IMAGE_PIXELS,
        100_000_000,
    ),
    "YOLO_BOOK_IMAGE_SIZE": (YOLO_BOOK_IMAGE_SIZE, 4096),
    "YOLO_IMAGE_SIZE": (YOLO_IMAGE_SIZE, 4096),
    "YOLO_MAX_PER_MODEL": (YOLO_MAX_PER_MODEL, 100),
    "YOLO_MAX_ENSEMBLE_CANDIDATES": (
        YOLO_MAX_ENSEMBLE_CANDIDATES,
        100,
    ),
}

for setting_name, (setting_value, upper_bound) in (
    _POSITIVE_BOUNDED_SETTINGS.items()
):
    if not 1 <= setting_value <= upper_bound:
        raise ValueError(
            f"{setting_name}는 1과 {upper_bound} 사이여야 합니다."
        )

# 일반 결함과 구체 결함이 같은 물리 파손을 가리킬 때 이중 감점 방지
FINAL_DEFECT_DEDUP_FAMILY = {
    "COVER_TEAR": "PHYSICAL_EDGE_DAMAGE",
    "CORNER_CRUSH": "PHYSICAL_EDGE_DAMAGE",
    "EDGE_WEAR": "PHYSICAL_EDGE_DAMAGE",
    "OTHER_VISIBLE_DAMAGE": "PHYSICAL_EDGE_DAMAGE",
}


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
            "models/general_binary_team_s3_v2_best.pt"
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


def summarize_score_breakdown(
    score_breakdown: list[dict] | None,
) -> str:
    """관리자 로그에 표시할 결함별 감점 요약."""

    summaries = []

    for item in score_breakdown or []:
        if not isinstance(item, dict):
            continue

        if item.get("fatal"):
            penalty = "즉시반려"
        else:
            penalty = f"-{item.get('applied_penalty')}점"

        summaries.append(
            f"{item.get('type', 'UNKNOWN')} {penalty}"
        )

    return ", ".join(summaries) or "감점 없음"


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
            "general_binary,doodle",
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

@lru_cache(maxsize=1)
def get_book_detector() -> YOLO:
    """YOLO-World 책 영역 탐지 모델의 최초 1회 로딩."""

    model_path = resolve_model_path(
        YOLO_BOOK_MODEL_PATH
    )

    if not model_path.is_file():
        raise FileNotFoundError(
            "책 영역 탐지 모델 파일이 없습니다: "
            f"{model_path}"
        )

    model = YOLO(str(model_path))

    if not hasattr(model, "set_classes"):
        raise TypeError(
            "책 영역 탐지 모델은 YOLO-World여야 합니다: "
            f"{model_path}"
        )

    model.set_classes([YOLO_BOOK_PROMPT])
    return model


def detect_book_region(
    image: Image.Image,
    image_index: int,
) -> dict:
    """사진에서 가장 큰 책의 전체 영역 탐지."""

    model = get_book_detector()

    width, height = image.size

    result = model.predict(
        source=image,
        conf=YOLO_BOOK_CONFIDENCE,
        iou=0.50,
        imgsz=YOLO_BOOK_IMAGE_SIZE,
        max_det=3,
        device=os.getenv("YOLO_DEVICE", "cpu"),
        verbose=False,
    )[0]

    if (
        result.boxes is None
        or len(result.boxes) == 0
    ):
        return {
            "image_index": image_index,
            "image_view": IMAGE_VIEWS[image_index],
            "source_model": "yolo_world_book_detector",
            "detected": False,
            "confidence": None,
            "bbox": None,
            "pixel_bbox": None,
            "area_ratio": 0.0,
            "usable": False,
            "fallback_used": True,
        }

    boxes = result.boxes.xyxy.cpu().tolist()
    confidences = (
        result.boxes.conf.cpu().tolist()
    )

    # 여러 책이 잡히면 면적과 신뢰도가 가장 큰 책 선택
    box, confidence = max(
        zip(boxes, confidences),
        key=lambda item: (
            (item[0][2] - item[0][0])
            * (item[0][3] - item[0][1])
            * item[1]
        ),
    )

    x1, y1, x2, y2 = box

    padding_x = (
        (x2 - x1) * YOLO_BOOK_PADDING
    )
    padding_y = (
        (y2 - y1) * YOLO_BOOK_PADDING
    )

    x1 = max(0, int(round(x1 - padding_x)))
    y1 = max(0, int(round(y1 - padding_y)))
    x2 = min(width, int(round(x2 + padding_x)))
    y2 = min(height, int(round(y2 + padding_y)))
    area_ratio = (
        (x2 - x1)
        * (y2 - y1)
        / (width * height)
    )

    usable = (
        area_ratio
        >= YOLO_BOOK_MIN_AREA_RATIO
    )

    return {
        "image_index": image_index,
        "image_view": IMAGE_VIEWS[image_index],
        "source_model": "yolo_world_book_detector",
        "detected": True,
        "confidence": round(
            float(confidence),
            6,
        ),
        "bbox": [
            round(x1 / width, 6),
            round(y1 / height, 6),
            round(x2 / width, 6),
            round(y2 / height, 6),
        ],
        "pixel_bbox": [x1, y1, x2, y2],
        "area_ratio": round(
            area_ratio,
            6,
        ),
        "usable": usable,
        "fallback_used": not usable,
    }


def make_full_image_region(
    image: Image.Image,
    image_index: int,
) -> dict:
    """속지 사진의 전체 영역 사용 정보."""

    width, height = image.size

    return {
        "image_index": image_index,
        "image_view": IMAGE_VIEWS[image_index],
        "source_model": "full_image_inner",
        "detected": None,
        "confidence": None,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "pixel_bbox": [0, 0, width, height],
        "area_ratio": 1.0,
        "usable": True,
        "fallback_used": False,
    }


def evaluate_book_spatial_gate(
    candidate_bbox: list[float],
    book_region: dict,
) -> tuple[bool, float, bool]:
    """YOLO-World 책 영역을 이용한 배경 후보 차단."""

    if (
        book_region.get("image_view") == "INNER"
        or book_region.get("fallback_used")
        or not book_region.get("bbox")
    ):
        return True, 1.0, True

    x1, y1, x2, y2 = candidate_bbox
    bx1, by1, bx2, by2 = book_region["bbox"]

    intersection_width = max(
        0.0,
        min(x2, bx2) - max(x1, bx1),
    )
    intersection_height = max(
        0.0,
        min(y2, by2) - max(y1, by1),
    )
    candidate_area = max(
        (x2 - x1) * (y2 - y1),
        1e-12,
    )
    coverage = (
        intersection_width
        * intersection_height
        / candidate_area
    )
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    center_inside = (
        bx1 <= center_x <= bx2
        and by1 <= center_y <= by2
    )

    return (
        center_inside
        or coverage >= YOLO_BOOK_CANDIDATE_MIN_COVERAGE,
        round(coverage, 6),
        center_inside,
    )


def calculate_bbox_area_ratio(
    candidate_bbox: list[float],
    book_bbox: list[float],
) -> float:
    """전체 책 면적 대비 결함 BBox 면적 계산."""

    x1 = max(candidate_bbox[0], book_bbox[0])
    y1 = max(candidate_bbox[1], book_bbox[1])
    x2 = min(candidate_bbox[2], book_bbox[2])
    y2 = min(candidate_bbox[3], book_bbox[3])

    defect_area = (
        max(0.0, x2 - x1)
        * max(0.0, y2 - y1)
    )
    book_area = (
        max(0.0, book_bbox[2] - book_bbox[0])
        * max(0.0, book_bbox[3] - book_bbox[1])
    )

    if book_area <= 0:
        raise ValueError(
            "책 영역 BBox 면적이 올바르지 않습니다."
        )

    return round(
        min(100.0, defect_area / book_area * 100),
        2,
    )


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

# 검수 이미지 최대 허용 용량
MAX_INSPECTION_IMAGE_BYTES = 15 * 1024 * 1024


class _RejectRedirectHandler(HTTPRedirectHandler):
    """허용된 CloudFront URL이 다른 호스트로 우회되지 않게 차단."""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        raise ValueError(
            "검수 이미지 URL 리다이렉트는 허용되지 않습니다."
        )


def _load_inspection_image(
    raw_path: str,
) -> Image.Image:
    """CloudFront URL 또는 로컬 경로의 검수 이미지 로드."""

    if not isinstance(raw_path, str):
        raise ValueError(
            "검수 이미지 경로는 문자열이어야 합니다."
        )

    source = raw_path.strip()

    if not source:
        raise ValueError(
            "검수 이미지 경로가 비어 있습니다."
        )

    parsed_url = urlsplit(source)

    # 프론트에서 전달된 CloudFront HTTPS 이미지 처리
    if parsed_url.scheme in {"http", "https"}:
        # 실제 원격 이미지 검증 시점에만 백엔드 설정 로드
        from app.domains.inspections.inspection_image_service import (
            normalize_cloudfront_image_urls,
        )

        if parsed_url.scheme != "https":
            raise ValueError(
                "검수 이미지 URL은 HTTPS만 허용됩니다."
            )

        # 허용된 CloudFront 주소인지 기존 백엔드 정책으로 재검증
        image_url = normalize_cloudfront_image_urls(
            [source]
        )[0]

        request = Request(
            image_url,
            headers={
                "User-Agent": "wms-ai-vision/1.0",
            },
        )

        with build_opener(
            _RejectRedirectHandler()
        ).open(
            request,
            timeout=20,
        ) as response:
            image_bytes = response.read(
                MAX_INSPECTION_IMAGE_BYTES + 1
            )

        if len(image_bytes) > MAX_INSPECTION_IMAGE_BYTES:
            raise ValueError(
                "검수 이미지가 15MB 제한을 초과했습니다."
            )

        image_source = BytesIO(
            image_bytes
        )

    # PowerShell 테스트용 로컬 이미지는 명시된 루트 안에서만 허용
    else:
        configured_root = os.getenv(
            "INSPECTION_LOCAL_IMAGE_ROOT",
            "",
        ).strip()
        if not configured_root:
            raise ValueError(
                "로컬 검수 이미지는 비활성화되어 있습니다."
            )

        allowed_root = Path(configured_root).resolve(
            strict=True
        )
        if not allowed_root.is_dir():
            raise ValueError(
                "INSPECTION_LOCAL_IMAGE_ROOT는 디렉터리여야 합니다."
            )

        requested_path = Path(source)
        if not requested_path.is_absolute():
            requested_path = allowed_root / requested_path

        try:
            image_path = requested_path.resolve(
                strict=True
            )
            image_path.relative_to(allowed_root)
        except (FileNotFoundError, ValueError) as error:
            raise ValueError(
                "허용된 로컬 검수 이미지 경로가 아닙니다."
            ) from error

        if not image_path.is_file():
            raise FileNotFoundError(
                "검수 이미지 파일이 없습니다."
            )

        image_source = image_path

    try:
        with Image.open(
            image_source
        ) as image:
            width, height = image.size
            if width * height > MAX_INSPECTION_IMAGE_PIXELS:
                raise ValueError(
                    "검수 이미지 픽셀 수가 허용 한도를 초과했습니다."
                )
            image.load()
            return ImageOps.exif_transpose(
                image
            ).convert("RGB")

    except OSError as error:
        raise ValueError(
            "검수 이미지 파일을 해석할 수 없습니다."
        ) from error

def book_detector_node(
    state: WMSInspectionState,
) -> WMSInspectionState:
    """사진별 책 영역을 찾아 Vision 분석 범위를 준비."""

    print("[Node] Book Detector 실행...")
    image_paths = state.get("image_paths") or []

    if len(image_paths) != len(IMAGE_VIEWS):
        message = "앞면, 뒷면, 속지 이미지가 정확히 3장 필요합니다."
        return {
            "book_regions": [],
            "repair_directive": message,
            "messages": [
                AIMessage(
                    content=f"[Book Detector] 실패 - {message}"
                )
            ],
        }

    book_regions: list[dict] = []

    try:
        for image_index, raw_path in enumerate(image_paths):
            image = _load_inspection_image(raw_path)
            image_view = IMAGE_VIEWS[image_index]

            # 펼친 속지는 COCO book 탐지가 불안정하므로 전체 이미지 사용
            if image_view == "INNER":
                book_regions.append(
                    make_full_image_region(image, image_index)
                )
                continue

            detected_region = detect_book_region(
                image,
                image_index,
            )

            if detected_region.get("usable"):
                book_regions.append(detected_region)
                continue

            # 탐지 실패 시 전체 사진으로 분석을 계속하는 안전한 대체 경로
            fallback_region = make_full_image_region(
                image,
                image_index,
            )
            fallback_region.update({
                "source_model": "full_image_fallback",
                "detected": detected_region.get("detected"),
                "confidence": detected_region.get("confidence"),
                "fallback_used": True,
                "fallback_reason": "BOOK_REGION_NOT_USABLE",
                "detector_bbox": detected_region.get("bbox"),
                "detector_pixel_bbox": detected_region.get(
                    "pixel_bbox"
                ),
                "detector_area_ratio": detected_region.get(
                    "area_ratio"
                ),
            })
            book_regions.append(fallback_region)

    except Exception as error:
        message = "책 영역 탐지 중 기술 오류가 발생했습니다."
        trace_event(
            "BOOK_DETECTOR_FAILED",
            {"error_type": type(error).__name__},
        )
        return {
            "book_regions": [],
            "repair_directive": message,
            "messages": [
                AIMessage(
                    content=f"[Book Detector] 실패 - {message}"
                )
            ],
        }

    fallback_views = [
        region["image_view"]
        for region in book_regions
        if region.get("fallback_used") is True
    ]
    trace_event(
        "BOOK_DETECTOR_COMPLETED",
        {
            "book_regions": book_regions,
            "fallback_views": fallback_views,
        },
    )

    return {
        "book_regions": book_regions,
        "repair_directive": None,
        "messages": [
            AIMessage(
                content=(
                    "[Book Detector] 완료 - "
                    f"fallback={fallback_views}"
                )
            )
        ],
    }

def image_to_data_url(
    image: Image.Image,
    *,
    max_side: int = 1280,
    quality: int = 80,
) -> str:
    """VLM 전송용 이미지 축소 및 JPEG 변환."""

    converted = image.convert("RGB")

    # 원본 비율을 유지한 VLM 입력 크기 제한
    converted.thumbnail(
        (max_side, max_side),
        Image.Resampling.LANCZOS,
    )

    buffer = BytesIO()
    converted.save(
        buffer,
        format="JPEG",
        quality=quality,
        optimize=True,
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

def deduplicate_confirmed_defects(
    defects: list[dict],
) -> list[dict]:
    """동일 사진의 같은 결함 영역 중 신뢰도가 높은 한 건만 유지."""

    kept: list[dict] = []

    def defect_family(defect: dict) -> str | None:
        defect_type = (
            defect.get("defect_type")
            or defect.get("type")
        )
        return FINAL_DEFECT_DEDUP_FAMILY.get(
            defect_type,
            defect_type,
        )

    def confidence_key(
        defect: dict,
    ) -> tuple[int, float, float, float]:
        defect_type = (
            defect.get("defect_type")
            or defect.get("type")
        )
        return (
            int(defect_type != "OTHER_VISIBLE_DAMAGE"),
            float(
                defect.get("vlm_confidence")
                or defect.get("confidence")
                or 0.0
            ),
            float(
                defect.get("ensemble_confidence")
                or 0.0
            ),
            float(
                defect.get("yolo_confidence")
                or 0.0
            ),
        )

    for defect in defects:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if (
                    defect.get("image_index")
                    == existing.get("image_index")
                    and defect_family(defect)
                    == defect_family(existing)
                    and calculate_bbox_iou(
                        defect["bbox"],
                        existing["bbox"],
                    ) >= FINAL_DEFECT_DEDUP_IOU
                )
            ),
            None,
        )

        if duplicate_index is None:
            kept.append(defect)
            continue

        if confidence_key(defect) > confidence_key(
            kept[duplicate_index]
        ):
            kept[duplicate_index] = defect

    return kept


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
    """YOLO 후보 중 구체적인 최고 신뢰도 클래스 선택."""

    specific_detections = [
        item
        for item in detections
        if item["mapped_type"]
        != "OTHER_VISIBLE_DAMAGE"
    ]

    selectable = (
        specific_detections
        or detections
    )

    return max(
        selectable,
        key=lambda item: item["confidence"],
    )["mapped_type"]


def detect_yolo_candidates(
    image: Image.Image,
    image_index: int,
    book_region: dict,
) -> tuple[list[dict], list[dict]]:
    """
    원본 사진에서 담당 YOLO 모델을 실행하고
    책 안의 겹치는 BBox를 후보 하나로 합친다.
    """

    width, height = image.size
    raw_detections: list[dict] = []

    for model_name, item in get_yolo_models().items():
        spec: YoloModelSpec = item["spec"]
        model: YOLO = item["model"]

        is_inner = image_index == 2

        # 속지는 Doodle 전용, 외부 사진은 물리 결함 모델 전용
        if (
            is_inner
            and spec.role != "DOODLE_SPECIALIST"
        ) or (
            not is_inner
            and spec.role == "DOODLE_SPECIALIST"
        ):
            continue

        model_confidence = float(
            os.getenv(
                f"YOLO_{model_name.upper()}_CONFIDENCE",
                str(spec.confidence),
            )
        )
        model_nms_iou = float(
            os.getenv("YOLO_MODEL_NMS_IOU", "0.50")
        )
        if not 0.0 <= model_confidence <= 1.0:
            raise ValueError(
                f"YOLO_{model_name.upper()}_CONFIDENCE는 "
                "0과 1 사이여야 합니다."
            )
        if not 0.0 <= model_nms_iou <= 1.0:
            raise ValueError(
                "YOLO_MODEL_NMS_IOU는 0과 1 사이여야 합니다."
            )

        result = model.predict(
            source=image,
            conf=model_confidence,
            iou=model_nms_iou,
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

            normalized_bbox = [
                round(x1 / width, 6),
                round(y1 / height, 6),
                round(x2 / width, 6),
                round(y2 / height, 6),
            ]
            (
                spatial_gate_passed,
                book_coverage,
                book_center_inside,
            ) = evaluate_book_spatial_gate(
                normalized_bbox,
                book_region,
            )

            raw_detections.append({
                "image_index": image_index,
                "image_view": IMAGE_VIEWS[image_index],
                "source_model": model_name,
                "source_role": spec.role,
                "source_class": source_class,
                "source_class_id": class_id,
                "mapped_type": mapped_type,
                "confidence": round(
                    float(confidence),
                    6,
                ),
                "bbox": normalized_bbox,
                "coordinate_space": (
                    "ORIGINAL_IMAGE_NORMALIZED"
                ),
                "book_spatial_gate_passed": spatial_gate_passed,
                "book_coverage": book_coverage,
                "book_center_inside": book_center_inside,
                "pixel_bbox": [
                    x1,
                    y1,
                    x2,
                    y2,
                ],
            })

    candidates: list[dict] = []

    eligible_detections = [
        item
        for item in raw_detections
        if item["book_spatial_gate_passed"]
    ]

    clusters = merge_model_detections(
        eligible_detections
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

        box_width = x2 - x1
        box_height = y2 - y1
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2

        target_width = min(
            width,
            max(
                VLM_CROP_MIN_SIDE,
                int(
                    box_width
                    * VLM_CROP_CONTEXT_SCALE
                ),
            ),
        )
        target_height = min(
            height,
            max(
                VLM_CROP_MIN_SIDE,
                int(
                    box_height
                    * VLM_CROP_CONTEXT_SCALE
                ),
            ),
        )

        crop_x1 = int(
            min(
                max(0, center_x - target_width / 2),
                width - target_width,
            )
        )
        crop_y1 = int(
            min(
                max(0, center_y - target_height / 2),
                height - target_height,
            )
        )

        crop = image.crop((
            crop_x1,
            crop_y1,
            crop_x1 + target_width,
            crop_y1 + target_height,
        ))

        specialist_types = {
            detection["mapped_type"]
            for detection in cluster
            if detection["mapped_type"]
            != "OTHER_VISIBLE_DAMAGE"
        }

        candidates.append({
            "candidate_id": candidate_id,
            "image_view": IMAGE_VIEWS[image_index],
            "image_index": image_index,
            "bbox": bbox,
            "coordinate_space": (
                "ORIGINAL_IMAGE_NORMALIZED"
            ),
            "book_coverage": max(
                detection["book_coverage"]
                for detection in cluster
            ),
            "book_center_inside": any(
                detection["book_center_inside"]
                for detection in cluster
            ),
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
            outline="#ff8c00",
            width=5,
        )
        draw.text(
            (x1 + 5, y1 + 5),
            f"candidate #{candidate_id}",
            fill="#ff8c00",
            stroke_width=2,
            stroke_fill="white",
        )
    return annotated


def draw_defects(
    image: Image.Image,
    defects: list[dict],
) -> Image.Image:
    """종합 검증용 원본 사진에 전역 결함 인덱스를 표시."""

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    width, height = image.size

    for defect in defects:
        x1, y1, x2, y2 = defect["bbox"]
        box = [
            int(x1 * width),
            int(y1 * height),
            int(x2 * width),
            int(y2 * height),
        ]
        defect_index = defect["validation_index"]

        draw.rectangle(
            box,
            outline="#ff0055",
            width=5,
        )
        draw.text(
            (box[0] + 5, box[1] + 5),
            f"defect #{defect_index}",
            fill="#ff0055",
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
        if key not in {
            "crop",
            "source_predictions",
        }
    }

def vision_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    학습셋 보유 여부로 경로를 나눈 뒤 한 번 종합 검증합니다.

    - FRONT/BACK: YOLO 결정론 경로
    - INNER: Doodle 힌트 + GPT-4o 전체 판독
    - 합류: GPT-4o-mini 독립 검증
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

    if state.get("human_feedback") == "RE_CHECK":
        revision_count = 0

    downstream_reset = {
        "is_mint": None,
        "ubci_score": None,
        "provisional_ubci_score": None,
        "predicted_grade": None,
        "score_breakdown": None,
        "provisional_score_breakdown": None,
        "fatal_defect_detected": None,
        "grade_reason_code": None,
        "rule_reference": None,
        "policy_confidence": None,
        "policy_evidence": None,
        "policy_rag_status": None,
        "policy_rag_domains": None,
        "overall_confidence": None,
        "human_feedback": None,
        "primary_reason_code": (
            state.get("primary_reason_code")
            if state.get("human_feedback") == "RE_CHECK"
            else None
        ),
        "target_grade": None,
        "final_grade": None,
        "final_report": None,
    }

    def failure_result(message: str) -> WMSInspectionState:
        result = {
            **downstream_reset,
            "yolo_model_manifest": None,
            "book_regions": state.get("book_regions") or [],
            "raw_yolo_detections": [],
            "ensemble_candidates": [],
            "reviewed_candidates": [],
            "rejected_candidates": [],
            "uncertain_candidates": [],
            "missed_defect_suspected": False,
            "vision_observations": [],
            "defects": None,
            "image_quality_ok": False,
            "vision_confidence": None,
            "vision_status": "FAILED",
            "vision_reason_code": "QUALITY_ERROR",
            "reason_code": None,
            "repair_directive": message,
            "revision_count": revision_count + 1,
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
                "vision_reason_code": "QUALITY_ERROR",
                "repair_directive": message,
                "revision_count": result["revision_count"],
            },
        )
        return result

    if len(image_paths) != len(IMAGE_VIEWS):
        return failure_result(
            "앞면, 뒷면, 속지 이미지가 정확히 3장 필요합니다."
        )

    stored_book_regions = state.get("book_regions")
    if (
        type(stored_book_regions) is not list
        or len(stored_book_regions) != len(IMAGE_VIEWS)
    ):
        return failure_result(
            "Book Detector 결과가 없거나 불완전합니다."
        )

    region_by_index = {
        region.get("image_index"): dict(region)
        for region in stored_book_regions
        if type(region) is dict
    }
    if set(region_by_index) != set(range(len(IMAGE_VIEWS))):
        return failure_result(
            "Book Detector 사진 인덱스가 올바르지 않습니다."
        )

    book_regions = [
        region_by_index[index]
        for index in range(len(IMAGE_VIEWS))
    ]

    full_image_prompt = """
당신은 학습셋이 없는 도서 촬영면을 전체 판독하는 Vision Agent입니다.

[입력]
- 사진 유형은 INNER입니다.
- Doodle YOLO 후보는 필기 위치를 찾기 위한 힌트일 뿐입니다.
- 전체 원본 사진과 후보 확대 이미지를 함께 봅니다.

[해야 할 일]
1. 후보 유무와 관계없이 원본 전체에서 보이는 모든 결함을 찾습니다.
2. 결함마다 새로운 BBox를 직접 만들 수 있습니다.
3. BBox는 원본 사진 기준 [x_min, y_min, x_max, y_max] 0~1 좌표입니다.
4. image_index는 반드시 2, location은 원칙적으로 INNER_PAGE입니다.
5. observation에 실제로 보이는 근거를 짧게 작성합니다.
6. 인쇄된 글자, 문제, 표, 선과 손글씨를 구분합니다.

[허용 결함 종류]
COVER_SCRATCH, COVER_TEAR, STICKER_MARK, CORNER_CRUSH, EDGE_WEAR,
SPINE_CRACKING, LOOSE_BINDING, GENERAL_STAIN, FADING, SIGNATURE,
LIBRARY_STAMP, WATER_DAMAGE, PAGE_WARPING, PAGE_FOLD, WRITING,
HIGHLIGHTING, BARCODE_DAMAGE, OTHER_VISIBLE_DAMAGE

[판독 원칙]
- Doodle 후보가 없어도 오염, 찢김, 접힘, 변색, 필기 등을 직접 찾습니다.
- Doodle 후보는 위치 힌트일 뿐이며, 후보의 존재 자체는 WRITING 근거가 아닙니다.
- 인쇄 정렬과 다른 자유로운 획일 때만 WRITING입니다.
- 악보 기호, 인쇄된 답안·문항·표·밑줄·장식은 WRITING이 아닙니다.
- 조명, 그림자, 인쇄물은 결함으로 만들지 않습니다.
- 흐리거나 가려져 확정할 수 없으면 임의 결함을 만들지 말고
  image_quality_ok 또는 review_confidence에 반영합니다.
- ratio는 대략 반환하되 서버가 BBox 기준으로 다시 계산합니다.
"""

    combined_prompt = """
당신은 두 Vision 경로가 합류한 뒤 결과를 독립 검증하는 검수자입니다.

[입력]
- FRONT/BACK은 학습 모델이 만든 결정론적 결함입니다.
- INNER는 GPT-4o가 전체 판독한 결함입니다.
- 각 사진에는 defect #번호가 표시되어 있습니다.
- 앞선 추론 과정은 제공되지 않고 원본 증거와 확정 후보 목록만 제공됩니다.

[규칙]
1. 모든 defect_index를 정확히 한 번씩 검증합니다.
2. 새로운 BBox나 결함 종류를 만들거나 기존 좌표를 변경하지 않습니다.
3. CONFIRMED, REJECTED, UNCERTAIN 중 하나로 판정합니다.
4. 표지 디자인, 인쇄물, 조명, 그림자는 REJECTED입니다.
5. 흐림·가림·근거 부족은 UNCERTAIN입니다.
6. 목록 밖에 명확한 추가 결함이 보일 때만
   missed_defect_suspected=true로 반환합니다.
7. review_confidence는 결함 확률이 아니라 현재 검증의 확실성입니다.
8. 기존 결함 종류와 YOLO 신뢰도를 사실로 전제하지 말고 원본에서 독립 검증합니다.
9. WRITING은 인쇄 정렬과 다른 자유로운 획이 실제로 보일 때만 CONFIRMED입니다.
   악보 기호, 인쇄된 답안·문항·표·밑줄·장식은 REJECTED입니다.
10. WRITING/HIGHLIGHTING 후보가 인쇄된 내용뿐이면
    printed_content_only=true로 표시하고 REJECTED로 판정합니다.
"""

    try:
        model_manifest = get_yolo_model_manifest()
        full_image_model = ChatOpenAI(
            model=os.getenv(
                "OPENAI_DEFECT_MODEL",
                os.getenv(
                    "OPENAI_VISION_MODEL",
                    "gpt-4o",
                ),
            ),
            temperature=0,
            timeout=90,
            max_retries=1,
        ).with_structured_output(
            FullImageVisionReview,
            method="json_schema",
        )
        combined_model = ChatOpenAI(
            model=os.getenv(
                "OPENAI_VISION_VALIDATOR_MODEL",
                "gpt-4o-mini",
            ),
            temperature=0,
            timeout=60,
            max_retries=1,
        ).with_structured_output(
            CombinedVisionReview,
            method="json_schema",
        )

        all_raw_detections: list[dict] = []
        all_ensemble_candidates: list[dict] = []
        reviewed_candidates: list[dict] = []
        rejected_candidates: list[dict] = []
        uncertain_candidates: list[dict] = []
        preliminary_defects: list[dict] = []
        vision_observations: list[dict] = []
        source_images: list[Image.Image] = []
        inner_image_quality_ok = True
        inner_confidences: list[float] = []

        for image_index, raw_path in enumerate(image_paths):
            image = _load_inspection_image(raw_path)
            source_images.append(image)
            image_view = IMAGE_VIEWS[image_index]
            book_region = book_regions[image_index]

            raw_detections, candidates = detect_yolo_candidates(
                image,
                image_index,
                book_region,
            )
            annotated = draw_candidates(image, candidates)
            spatial_candidate_count = sum(
                bool(item["book_spatial_gate_passed"])
                for item in raw_detections
            )

            book_region.update({
                "crop_applied": False,
                "analysis_mode": (
                    "TRAINED_DETERMINISTIC_YOLO"
                    if image_view in TRAINED_MODEL_VIEWS
                    else "UNTRAINED_FULL_IMAGE_VLM"
                ),
                "analysis_skipped": False,
                "candidate_count_before": len(raw_detections),
                "candidate_count_after": spatial_candidate_count,
                "spatially_rejected_count": (
                    len(raw_detections)
                    - spatial_candidate_count
                ),
                "ensemble_candidate_count": len(candidates),
            })

            safe_candidates = [
                state_safe_candidate(candidate)
                for candidate in candidates
            ]
            all_raw_detections.extend(raw_detections)
            all_ensemble_candidates.extend(safe_candidates)

            trace_event(
                "YOLO_ENSEMBLE_COMPLETED",
                {
                    "image_index": image_index,
                    "image_view": image_view,
                    "route": book_region["analysis_mode"],
                    "coordinate_space": "ORIGINAL_IMAGE",
                    "book_region": book_region,
                    "raw_detections": raw_detections,
                    "ensemble_candidates": safe_candidates,
                },
            )

            if image_view in TRAINED_MODEL_VIEWS:
                location = (
                    "FRONT_COVER"
                    if image_view == "FRONT"
                    else "BACK_COVER"
                )

                for candidate in candidates:
                    proposed_type = candidate["proposed_type"]
                    if proposed_type == "CORNER_CRUSH":
                        defect_location = "CORNER"
                    elif proposed_type == "EDGE_WEAR":
                        defect_location = "BOOK_EDGE"
                    elif proposed_type in {
                        "SPINE_CRACKING",
                        "LOOSE_BINDING",
                    }:
                        defect_location = "SPINE"
                    else:
                        defect_location = location

                    defect = DefectOutput(
                        type=proposed_type,
                        location=defect_location,
                        bbox=candidate["bbox"],
                        ratio=calculate_bbox_area_ratio(
                            candidate["bbox"],
                            book_region["bbox"],
                        ),
                        confidence=float(
                            candidate["ensemble_confidence"]
                        ),
                        image_index=image_index,
                        observation=(
                            "학습 모델의 결정론적 결함 후보"
                        ),
                    ).model_dump()
                    defect.update({
                        "image_view": image_view,
                        "image_url": raw_path,
                        "defect_type": proposed_type,
                        "coordinate_space": (
                            "ORIGINAL_IMAGE_NORMALIZED"
                        ),
                        "book_coverage": candidate["book_coverage"],
                        "ratio_source": (
                            "BOOK_REGION_BBOX_AREA"
                            if not book_region.get("fallback_used")
                            else "ORIGINAL_IMAGE_BBOX_AREA"
                        ),
                        "candidate_id": candidate["candidate_id"],
                        "proposed_type": proposed_type,
                        "yolo_confidence": candidate["yolo_confidence"],
                        "ensemble_confidence": (
                            candidate["ensemble_confidence"]
                        ),
                        "source_models": candidate["source_models"],
                        "source_predictions": (
                            candidate["source_predictions"]
                        ),
                        "class_conflict": candidate["class_conflict"],
                        "validation_source": (
                            "TRAINED_DETERMINISTIC_YOLO"
                        ),
                    })
                    preliminary_defects.append(defect)
                    reviewed_candidates.append({
                        **state_safe_candidate(candidate),
                        "route_decision": "DETERMINISTIC",
                    })
                continue

            if image_view not in UNTRAINED_VLM_VIEWS:
                raise ValueError(
                    f"학습셋 라우팅이 정의되지 않은 사진: {image_view}"
                )

            candidate_metadata = [
                {
                    "candidate_id": candidate["candidate_id"],
                    "bbox": candidate["bbox"],
                    "yolo_confidence": candidate["yolo_confidence"],
                }
                for candidate in candidates
            ]
            content = [
                {
                    "type": "text",
                    "text": (
                        "Doodle 후보는 힌트이며 전체 속지를 "
                        "독립적으로 판독하세요.\n"
                        + json.dumps(
                            candidate_metadata,
                            ensure_ascii=False,
                        )
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_to_data_url(
                            annotated,
                            max_side=1600,
                            quality=85,
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
                            "Doodle 힌트 확대 이미지 "
                            f"candidate_id={candidate['candidate_id']}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_to_data_url(
                                candidate["crop"],
                                max_side=512,
                                quality=80,
                            ),
                            "detail": "high",
                        },
                    },
                ])

            full_review = full_image_model.invoke([
                ("system", full_image_prompt),
                HumanMessage(content=content),
            ])
            inner_image_quality_ok = (
                inner_image_quality_ok
                and full_review.image_quality_ok
            )
            inner_confidences.append(
                full_review.review_confidence
            )
            vision_observations.extend(
                {
                    "image_index": image_index,
                    "image_view": image_view,
                    "observation": observation,
                    "source": "FULL_IMAGE_GPT4O",
                }
                for observation in full_review.observations
                if observation.strip()
            )

            for returned_defect in full_review.defects:
                if returned_defect.image_index != image_index:
                    raise ValueError(
                        "속지 전체 판독의 image_index가 올바르지 않습니다."
                    )

                defect = returned_defect.model_dump()
                defect["ratio"] = calculate_bbox_area_ratio(
                    defect["bbox"],
                    book_region["bbox"],
                )
                defect.update({
                    "image_view": image_view,
                    "image_url": raw_path,
                    "defect_type": defect["type"],
                    "coordinate_space": (
                        "ORIGINAL_IMAGE_NORMALIZED"
                    ),
                    "ratio_source": (
                        "ORIGINAL_IMAGE_BBOX_AREA"
                    ),
                    "vlm_confidence": defect["confidence"],
                    "validation_source": "FULL_IMAGE_GPT4O",
                    "doodle_hint_ids": [
                        candidate["candidate_id"]
                        for candidate in candidates
                        if calculate_bbox_iou(
                            defect["bbox"],
                            candidate["bbox"],
                        ) > 0
                    ],
                })
                preliminary_defects.append(defect)

            reviewed_candidates.extend(
                {
                    **state_safe_candidate(candidate),
                    "route_decision": "VLM_HINT_ONLY",
                }
                for candidate in candidates
            )

        preliminary_defects = deduplicate_confirmed_defects(
            preliminary_defects
        )
        for defect_index, defect in enumerate(
            preliminary_defects
        ):
            defect["validation_index"] = defect_index

        public_defects = [
            {
                key: value
                for key, value in defect.items()
                if key not in {
                    "image_url",
                    "source_predictions",
                }
            }
            for defect in preliminary_defects
        ]
        combined_content = [
            {
                "type": "text",
                "text": (
                    "다음 결함 목록을 세 원본 이미지와 대조하세요.\n"
                    + json.dumps(
                        public_defects,
                        ensure_ascii=False,
                        default=str,
                    )
                ),
            }
        ]

        for image_index, image in enumerate(source_images):
            image_defects = [
                defect
                for defect in preliminary_defects
                if defect["image_index"] == image_index
            ]
            combined_content.extend([
                {
                    "type": "text",
                    "text": (
                        f"사진 {image_index}: "
                        f"{IMAGE_VIEWS[image_index]}"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_to_data_url(
                            draw_defects(
                                image,
                                image_defects,
                            ),
                            max_side=1600,
                            quality=85,
                        ),
                        "detail": "high",
                    },
                },
            ])

        combined_review = combined_model.invoke([
            ("system", combined_prompt),
            HumanMessage(content=combined_content),
        ])
        expected_indices = set(
            range(len(preliminary_defects))
        )
        returned_indices = [
            item.defect_index
            for item in combined_review.reviews
        ]
        if (
            len(returned_indices) != len(set(returned_indices))
            or set(returned_indices) != expected_indices
        ):
            raise ValueError(
                "종합 검증이 결함 인덱스를 누락·중복·추가했습니다."
            )

        confidence_values = [
            combined_review.review_confidence,
            *inner_confidences,
        ]
        final_defects: list[dict] = []

        for item in combined_review.reviews:
            defect = dict(
                preliminary_defects[item.defect_index]
            )
            review_payload = item.model_dump()
            review_record = {
                key: value
                for key, value in {
                    **defect,
                    "combined_review": review_payload,
                }.items()
                if key not in {
                    "image_url",
                    "source_predictions",
                }
            }
            confidence_values.append(
                item.review_confidence
            )

            printed_content_false_positive = (
                defect.get("type") in {
                    "WRITING",
                    "HIGHLIGHTING",
                }
                and item.printed_content_only
            )

            if (
                item.decision == "REJECTED"
                or printed_content_false_positive
            ):
                rejected_candidates.append(review_record)
                continue

            if (
                item.decision == "UNCERTAIN"
                or item.review_confidence
                < MIN_VISION_CONFIDENCE
            ):
                uncertain_candidates.append(review_record)
                continue

            defect["confidence"] = (
                item.review_confidence
            )
            defect["combined_validation"] = (
                review_payload
            )
            defect.pop("validation_index", None)
            defect.pop("image_url", None)
            defect.pop("source_predictions", None)
            final_defects.append(defect)

        final_defects = deduplicate_confirmed_defects(
            final_defects
        )
        all_image_quality_ok = (
            inner_image_quality_ok
            and combined_review.image_quality_ok
        )
        missed_defect_suspected = (
            combined_review.missed_defect_suspected
        )
        vision_confidence = min(confidence_values)

    except Exception as error:
        error_type = type(error).__name__
        print(
            "[Agent] Two-track Vision 실패:",
            error_type,
        )
        trace_event(
            "VISION_ERROR",
            {
                "error_type": error_type,
            },
        )
        return failure_result(
            "Vision 처리 중 오류가 발생했습니다. "
            f"오류 유형: {error_type}"
        )

    vision_status = "COMPLETED"
    vision_reason_code = None
    repair_directive = None

    if not all_image_quality_ok:
        vision_status = "REVIEW_REQUIRED"
        vision_reason_code = "VISION_IMAGE_QUALITY"
        repair_directive = (
            "흐림, 가림, 역광 등으로 사진 판독이 어렵습니다. "
            "앞면, 뒷면, 속지를 다시 촬영해 주세요."
        )
        revision_count += 1
    elif missed_defect_suspected:
        vision_status = "REVIEW_REQUIRED"
        vision_reason_code = (
            "VISION_MISSED_DEFECT_SUSPECTED"
        )
        repair_directive = (
            "종합 검증에서 목록 밖 추가 결함이 의심됩니다. "
            "관리자 확인이 필요합니다."
        )
        revision_count += 1
    elif uncertain_candidates:
        vision_status = "REVIEW_REQUIRED"
        vision_reason_code = (
            "VISION_UNCERTAIN_CANDIDATE"
        )
        repair_directive = (
            "종합 검증이 확정하지 못한 결함이 있습니다. "
            "표시된 BBox를 관리자가 확인해야 합니다."
        )
        revision_count += 1
    elif vision_confidence < MIN_VISION_CONFIDENCE:
        vision_status = "REVIEW_REQUIRED"
        vision_reason_code = "VISION_LOW_CONFIDENCE"
        repair_directive = (
            "Vision 판단 신뢰도가 기준보다 낮아 "
            "관리자 확인이 필요합니다."
        )
        revision_count += 1

    result = {
        **downstream_reset,
        "is_mint": (
            not final_defects
            if vision_status == "COMPLETED"
            else None
        ),
        "yolo_model_manifest": model_manifest,
        "book_regions": book_regions,
        "raw_yolo_detections": all_raw_detections,
        "ensemble_candidates": all_ensemble_candidates,
        "reviewed_candidates": reviewed_candidates,
        "rejected_candidates": rejected_candidates,
        "uncertain_candidates": uncertain_candidates,
        "missed_defect_suspected": missed_defect_suspected,
        "vision_observations": vision_observations,
        "defects": final_defects,
        "image_quality_ok": all_image_quality_ok,
        "vision_confidence": vision_confidence,
        "vision_status": vision_status,
        "vision_reason_code": vision_reason_code,
        "reason_code": None,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "messages": [
            AIMessage(
                content=(
                    "[Vision Agent] 완료 - "
                    f"상태={vision_status}, "
                    "결함="
                    + (
                        ", ".join(
                            f"{defect_type}×"
                            f"{sum(1 for item in final_defects if item.get('type') == defect_type)}"
                            for defect_type in sorted({
                                str(item.get("type"))
                                for item in final_defects
                                if item.get("type")
                            })
                        )
                        or "없음"
                    )
                    + ", "
                    f"확정={len(final_defects)}, "
                    f"오탐제외={len(rejected_candidates)}, "
                    f"판정보류={len(uncertain_candidates)}, "
                    f"신뢰도={vision_confidence}, "
                    "YOLO="
                    + (
                        ", ".join(
                            f"{item.get('name')}="
                            f"{Path(str(item.get('path'))).name}"
                            for item in model_manifest
                        )
                        or "없음"
                    )
                    + ", 경로=FRONT/BACK→General, "
                    "INNER→Doodle+GPT-4o"
                )
            )
        ],
    }

    trace_event(
        "VISION_OUTPUT",
        {
            "vision_status": vision_status,
            "vision_reason_code": vision_reason_code,
            "image_quality_ok": all_image_quality_ok,
            "vision_confidence": vision_confidence,
            "model_manifest": model_manifest,
            "book_regions": book_regions,
            "raw_detection_count": len(
                all_raw_detections
            ),
            "ensemble_candidate_count": len(
                all_ensemble_candidates
            ),
            "confirmed_defects": [
                {
                    key: value
                    for key, value in defect.items()
                    if key not in {
                        "image_url",
                        "source_predictions",
                    }
                }
                for defect in final_defects
            ],
            "rejected_candidate_count": len(
                rejected_candidates
            ),
            "uncertain_candidate_count": len(
                uncertain_candidates
            ),
            "missed_defect_suspected": (
                missed_defect_suspected
            ),
            "repair_directive": repair_directive,
            "revision_count": revision_count,
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

# 현재 검수는 속지 한 장(펼침면)을 촬영하므로, 확정된 내지 훼손은
# UBCI v2의 "5장 이하 훼손" 구간으로 한 번만 감점합니다.
INNER_PAGE_DAMAGE_TYPES = frozenset({
    "WRITING",
    "HIGHLIGHTING",
    "PAGE_FOLD",
})
INNER_PAGE_DAMAGE_PENALTY = 10.0

HITL_REQUIRED_DEFECTS = {
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
            and defect_type not in INNER_PAGE_DAMAGE_TYPES
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

        penalty_group = (
            "INNER_PAGE_DAMAGE"
            if defect_type in INNER_PAGE_DAMAGE_TYPES
            else defect_type
        )

        grouped = grouped_defects.setdefault(
            penalty_group,
            {
                "ratio": 0.0,
                "text_overlap": False,
                "morphology_severe": False,
                "detected_types": set(),
            },
        )

        # 같은 종류의 결함이 여러 개라면 면적을 합산
        grouped["ratio"] += float(ratio)
        grouped["text_overlap"] |= text_overlap
        grouped["morphology_severe"] |= morphology_severe
        grouped["detected_types"].add(defect_type)

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

        if defect_type == "INNER_PAGE_DAMAGE":
            total_penalty += INNER_PAGE_DAMAGE_PENALTY
            score_breakdown.append({
                "type": defect_type,
                "detected_types": sorted(
                    defect["detected_types"]
                ),
                "total_ratio": total_ratio,
                "severity": "OBSERVED_LE_5_PAGES",
                "text_overlap": defect["text_overlap"],
                "applied_penalty": (
                    INNER_PAGE_DAMAGE_PENALTY
                ),
                "fatal": False,
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

    policy_evidence: list[dict] = []
    policy_rag_status = "RULE_ENGINE_FALLBACK"
    policy_rag_domains: list[str] = []

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
        try:
            policy_evidence = (
                search_policy_rules(
                    defects=defects,
                    policy_version=(
                        POLICY_VERSION
                    ),
                )
            )
            policy_rag_domains = sorted({
                str(item.get("policy_domain", ""))
                for item in policy_evidence
                if item.get("policy_domain")
            })
            if set(policy_rag_domains) == {
                "UBCI",
                "WMS_OPERATION",
            }:
                policy_rag_status = "USED"
        except Exception as error:
            print(
                "[Policy RAG] 검색 실패 - "
                f"{type(error).__name__}: "
                f"{error}"
            )
        # 결함이 없을 때 Policy가 MINT 확정
        if not defects:
            result = {
                "is_mint": True,
                "ubci_score": 100.0,
                "provisional_ubci_score": None,
                "predicted_grade": "S",
                "score_breakdown": [],
                "provisional_score_breakdown": None,
                "fatal_defect_detected": False,
                "grade_reason_code": (
                    "NO_VISIBLE_DEFECT"
                ),
                "rule_reference": (
                    POLICY_VERSION
                ),
                "policy_confidence": 1.0,
                "policy_evidence": policy_evidence,
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

            scorable_defects = [
                defect
                for defect in defects
                if defect.get("type")
                not in HITL_REQUIRED_DEFECTS
            ]

            (
                calculated_ubci_score,
                calculated_score_breakdown,
                fatal_defect_detected,
            ) = calculate_ubci_score(
                scorable_defects
            )

            if fatal_defect_detected:
                grade_reason_code = next(
                    item["type"]
                    for item in calculated_score_breakdown
                    if item["fatal"]
                )

                result = {
                    "is_mint": False,
                    "ubci_score": float(
                        calculated_ubci_score
                    ),
                    "provisional_ubci_score": None,
                    "predicted_grade": "REJECT",
                    "score_breakdown": (
                        calculated_score_breakdown
                    ),
                    "provisional_score_breakdown": None,
                    "fatal_defect_detected": True,
                    "grade_reason_code": grade_reason_code,
                    "rule_reference": POLICY_VERSION,
                    "policy_confidence": 1.0,
                    "reason_code": None,
                    "repair_directive": None,
                }

            elif manual_types:
                repair_directive = (
                    "UBCI 자동 감점 규칙이 정의되지 않아 "
                    "관리자 확인이 필요한 결함: "
                    + ", ".join(manual_types)
                )

                result = {
                    "is_mint": False,
                    "ubci_score": None,
                    "provisional_ubci_score": float(
                        calculated_ubci_score
                    ),
                    "predicted_grade": None,
                    "score_breakdown": None,
                    "provisional_score_breakdown": (
                        calculated_score_breakdown
                    ),
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
                    "repair_directive": repair_directive,
                }

            else:
                predicted_grade = (
                    calculate_ubci_grade(
                        calculated_ubci_score,
                        fatal_defect_detected,
                    )
                )

                grade_reason_code = max(
                    calculated_score_breakdown,
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
                        calculated_ubci_score
                    ),
                    "provisional_ubci_score": None,

                    "predicted_grade": (
                        predicted_grade
                    ),
                    "score_breakdown": (
                        calculated_score_breakdown
                    ),
                    "provisional_score_breakdown": None,
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
            "provisional_ubci_score": None,
            "predicted_grade": None,
            "score_breakdown": None,
            "provisional_score_breakdown": None,
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
        "policy_evidence": policy_evidence,
        "policy_rag_status": policy_rag_status,
        "policy_rag_domains": policy_rag_domains,
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
                    f"상태={result['reason_code'] or '정상'}, "
                    f"MINT={result['is_mint']}, "
                    f"UBCI={result['ubci_score']}, "
                    f"임시점수={result['provisional_ubci_score']}, "
                    f"등급={result['predicted_grade']}, "
                    "감점="
                    f"{summarize_score_breakdown(result['score_breakdown'] or result['provisional_score_breakdown'])}, "
                    f"Policy RAG={policy_rag_status}, "
                    "도메인="
                    f"{','.join(policy_rag_domains) or '없음'}, "
                    f"근거={len(policy_evidence)}건"
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
            "provisional_ubci_score": (
                output[
                    "provisional_ubci_score"
                ]
            ),
            "predicted_grade": (
                output["predicted_grade"]
            ),
            "score_breakdown": (
                output["score_breakdown"]
            ),
            "provisional_score_breakdown": (
                output[
                    "provisional_score_breakdown"
                ]
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
            "policy_evidence": [
                {
                    key: item.get(key)
                    for key in (
                        "rule_id",
                        "clause_ref",
                        "policy_version",
                        "policy_domain",
                        "source",
                    )
                }
                for item in output["policy_evidence"]
                if isinstance(item, dict)
            ],
            "policy_rag_status": (
                output["policy_rag_status"]
            ),
            "policy_rag_domains": (
                output["policy_rag_domains"]
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
    revision_count_is_valid = (
        type(raw_revision_count) is int
        and raw_revision_count >= 0
    )
    revision_count = (
        raw_revision_count
        if revision_count_is_valid
        else 0
    )

    is_mint = state.get("is_mint")
    defects = state.get("defects")
    vision_confidence = state.get(
        "vision_confidence"
    )
    ubci_score = state.get("ubci_score")
    predicted_grade = state.get("predicted_grade")
    score_breakdown = state.get("score_breakdown")
    fatal_defect_detected = state.get(
        "fatal_defect_detected"
    )
    rule_reference = state.get(
        "rule_reference"
    )
    policy_confidence = state.get(
        "policy_confidence"
    )

    reason_code = "OK"
    repair_directive = None
    overall_confidence = None

    # revision_count 타입 검증
    if not revision_count_is_valid:
        reason_code = "QUALITY_ERROR"
        repair_directive = (
            "revision_count는 0 이상의 "
            "정수여야 합니다."
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
    elif reason_code == "OK" and (
        type(ubci_score) not in (int, float)
        or not 0 <= ubci_score <= 100
    ):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "ubci_score는 0~100 범위의 숫자여야 합니다."

    # 정책 근거 검증
    elif reason_code == "OK" and rule_reference != POLICY_VERSION:
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = (
            "현재 서버 정책 버전과 rule_reference가 일치해야 합니다."
        )

    # Policy 신뢰도 검증
    elif reason_code == "OK" and (type(policy_confidence) not in (int, float) or not 0 <= policy_confidence <= 1):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "policy_confidence는 0~1 범위의 숫자여야 합니다."

    elif (
        reason_code == "OK"
        and policy_confidence
        < MIN_POLICY_CONFIDENCE
    ):
        reason_code = "POLICY_LOW_CONFIDENCE"
        repair_directive = (
            "Policy 검색 및 계산 신뢰도가 기준보다 낮습니다."
        )

    # Policy 결과를 결함 원본으로 독립 재계산해 변조·계산 오류를 차단
    if reason_code == "OK":
        try:
            (
                expected_score,
                expected_breakdown,
                expected_fatal,
            ) = calculate_ubci_score(defects)
            expected_grade = calculate_ubci_grade(
                expected_score,
                expected_fatal,
            )
        except (TypeError, ValueError) as error:
            reason_code = "UBCI_POLICY_VIOLATION"
            repair_directive = str(error)
        else:
            if (
                float(ubci_score) != expected_score
                or predicted_grade != expected_grade
                or score_breakdown != expected_breakdown
                or fatal_defect_detected is not expected_fatal
            ):
                reason_code = "UBCI_POLICY_VIOLATION"
                repair_directive = (
                    "Policy 점수·등급·감점 내역이 결함 원본의 "
                    "독립 재계산 결과와 일치하지 않습니다."
                )

    # RAG 미실행 기본 결과
    rag_result = {
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "critic_rag_used": False,
        "critic_retrieved_case_ids": [],
        "critic_retrieval_scores": [],
        "critic_retrieval_count": 0,
        "critic_decision_source": "RULE_ONLY",
        "critic_explanation": (
            "규칙 검증에서 오류가 발견되어 "
            "판례 검색을 실행하지 않았습니다."
        ),
        "critic_rag_confidence": None,
        "critic_prompt_version": (
            CRITIC_PROMPT_VERSION
        ),
    }

    if reason_code == "OK":
        overall_confidence = min(
            vision_confidence,
            policy_confidence,
        )

        # 기본 규칙 통과 후 판례 RAG 실행
        rag_result = evaluate_with_precedents(
            state
        )

        if rag_result["reason_code"] != "OK":
            reason_code = rag_result[
                "reason_code"
            ]
            repair_directive = rag_result[
                "repair_directive"
            ]
            overall_confidence = None
            revision_count += 1

    else:
        revision_count += 1

    return {
        **rag_result,
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "overall_confidence": (
            overall_confidence
        ),
        "final_report": None,
        "messages": [
            AIMessage(
                content=(
                    "[Critic Agent] 검증 결과 - "
                    f"상태={reason_code}, "
                    "판정소스="
                    f"{rag_result['critic_decision_source']}, "
                    "판례RAG="
                    f"{rag_result['critic_rag_used']}, "
                    "검색판례="
                    f"{rag_result['critic_retrieval_count']}건, "
                    "RAG신뢰도="
                    f"{rag_result['critic_rag_confidence']}, "
                    "설명="
                    f"{rag_result['critic_explanation']}"
                )
            )
        ],
    }

def _public_policy_evidence(
    state: WMSInspectionState,
    *,
    fallback_rule_id: str,
    fallback_clause_ref: str,
    fallback_source: str,
) -> list[dict]:
    """품질보증서 공개용 정책 근거 생성."""

    public_evidence = []

    for item in state.get("policy_evidence") or []:
        if not isinstance(item, dict):
            continue

        rule_id = item.get("rule_id")
        clause_ref = item.get("clause_ref")
        policy_version = item.get("policy_version")
        policy_domain = item.get("policy_domain")

        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                rule_id,
                clause_ref,
                policy_version,
            )
        ):
            continue

        safe_rule_id = {
            "UBCI": "UBCI_POLICY",
            "WMS_OPERATION": "WMS_OPERATION_POLICY",
        }.get(policy_domain)

        if safe_rule_id is None:
            safe_rule_id = (
                rule_id.strip()
                if rule_id in {
                    "UBCI_POLICY",
                    "WMS_OPERATION_POLICY",
                }
                else "POLICY_REFERENCE"
            )

        safe_clause_ref = clause_ref.strip()
        chunk_id = item.get("chunk_id")

        if (
            safe_clause_ref == chunk_id
            or re.fullmatch(
                r"(?:UBCI|WMS_OPERATION)_\d+",
                safe_clause_ref,
            )
        ):
            safe_clause_ref = "RETRIEVED_POLICY_CLAUSE"

        public_item = {
            "policy_version": policy_version.strip(),
            "rule_id": safe_rule_id,
            "clause_ref": safe_clause_ref,
            "source": {
                "UBCI": "UBCI_SPECIFICATION",
                "WMS_OPERATION": "WMS_OPERATION_POLICY",
            }.get(policy_domain, "POLICY_RAG"),
        }

        if public_item not in public_evidence:
            public_evidence.append(public_item)

    if public_evidence:
        return public_evidence

    return [{
        "policy_version": (
            state.get("rule_reference")
            or POLICY_VERSION
        ),
        "rule_id": fallback_rule_id,
        "clause_ref": fallback_clause_ref,
        "source": fallback_source,
    }]

def auto_refund_agent(state: WMSInspectionState) -> WMSInspectionState:
    """
    4. Auto-Refund Agent (Fast-track)
    TODO: MINT 등급의 새 책에 대한 환불 승인 사유서(JSON)를 작성하세요.
    - 출력: final_report (str, JSON format)
    """
    print("[Agent] Auto Refund Agent 실행...")

    is_mint = state.get("is_mint")
    defects = state.get("defects")
    vision_confidence = state.get("vision_confidence")

    # Vision MINT 입력 검증
    if (
        state.get("vision_status") != "COMPLETED"
        or state.get("image_quality_ok") is not True
        or state.get("missed_defect_suspected") is not False
        or state.get("uncertain_candidates") != []
        or state.get("human_feedback") is not None
        or is_mint is not True
        or type(defects) is not list
        or defects
    ):
        raise ValueError(
            "Auto Refund는 검증이 완료된 무결함 MINT 도서만 "
            "처리할 수 있습니다."
        )

    # Vision 신뢰도 검증
    if (
        type(vision_confidence) not in (int, float)
        or not MIN_VISION_CONFIDENCE
        <= vision_confidence
        <= 1
    ):
        raise ValueError(
            "Auto Refund에는 기준 이상의 "
            "vision_confidence가 필요합니다."
        )

    (
        ubci_score,
        score_breakdown,
        fatal_defect_detected,
    ) = calculate_ubci_score(defects)

    final_grade = calculate_ubci_grade(
        ubci_score,
        fatal_defect_detected,
    )

    overall_confidence = float(vision_confidence)

    policy_evidence = _public_policy_evidence(
        state,
        fallback_rule_id="NO_VISIBLE_DEFECT",
        fallback_clause_ref="MINT_FAST_TRACK",
        fallback_source="RULE_ENGINE",
    )

    report = {
        "result": "AUTO_REFUND_APPROVED",
        "decision": "AI_FAST_TRACK",
        "is_mint": True,
        "defects": [],
        "ubci_score": ubci_score,
        "provisional_ubci_score": None,
        "predicted_grade": final_grade,
        "final_grade": final_grade,
        "score_breakdown": score_breakdown,
        "provisional_score_breakdown": None,
        "fatal_defect_detected": fatal_defect_detected,
        "grade_reason_code": "NO_VISIBLE_DEFECT",
        "rule_reference": POLICY_VERSION,
        "policy_evidence": policy_evidence,
        "policy_rag_status": state.get(
            "policy_rag_status"
        ),
        "policy_rag_domains": state.get(
            "policy_rag_domains"
        ) or [],
        "reason_code": None,
        "vision_confidence": overall_confidence,
        "policy_confidence": None,
        "overall_confidence": overall_confidence,
        "message": (
            "앞면, 뒷면, 속지에서 확인 가능한 결함이 없어 "
            "MINT 자동 승인 처리되었습니다."
        ),
    }

    return {
        "is_mint": True,
        "ubci_score": ubci_score,
        "provisional_ubci_score": None,
        "predicted_grade": final_grade,
        "score_breakdown": [],
        "provisional_score_breakdown": None,
        "fatal_defect_detected": False,
        "grade_reason_code": "NO_VISIBLE_DEFECT",
        "rule_reference": POLICY_VERSION,
        "policy_confidence": None,
        "final_grade": final_grade,
        "final_report": json.dumps(
            report,
            ensure_ascii=False,
        ),
        "overall_confidence": overall_confidence,
        "human_feedback": None,
        "messages": [
            AIMessage(
                content=(
                    "[Auto Refund Agent] 완료 - "
                    f"UBCI={ubci_score}, "
                    f"최종등급={final_grade}, "
                    f"Vision신뢰도={overall_confidence}, "
                    f"정책근거={len(policy_evidence)}건, "
                    "MINT 자동 승인 및 품질보증서 생성"
                )
            )
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

    policy_evidence = _public_policy_evidence(
        state,
        fallback_rule_id=(
            state.get("grade_reason_code")
            or "UBCI_DETERMINISTIC_SCORE"
        ),
        fallback_clause_ref="UBCI_SCORE_CALCULATION",
        fallback_source="RULE_ENGINE_FALLBACK",
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
        "provisional_ubci_score": state.get(
            "provisional_ubci_score"
        ),
        "predicted_grade": predicted_grade,
        "final_grade": final_grade,
        "score_breakdown": (
            state.get("score_breakdown") or []
        ),
        "provisional_score_breakdown": (
            state.get(
                "provisional_score_breakdown"
            ) or []
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
        "policy_evidence": policy_evidence,
        "policy_rag_status": state.get(
            "policy_rag_status"
        ),
        "policy_rag_domains": state.get(
            "policy_rag_domains"
        ) or [],
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
                content=(
                    f"[Report Agent] 완료 - {message}, "
                    f"결과={result}, "
                    f"UBCI={state.get('ubci_score')}, "
                    f"최종등급={final_grade}, "
                    "감점="
                    f"{summarize_score_breakdown(state.get('score_breakdown'))}, "
                    f"정책근거={len(policy_evidence)}건, "
                    "Critic="
                    f"{state.get('critic_decision_source') or '없음'}"
                )
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
