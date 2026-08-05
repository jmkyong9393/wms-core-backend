import base64
import json
import os
import re

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
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

from .state import Grade, WMSInspectionState

POLICY_VERSION = "UBCI_SPEC_V2.0.0.0"

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

# 프론트 검수 이미지의 고정 순서
IMAGE_VIEWS = (
    "FRONT",
    "BACK",
    "INNER",
)

# 책 전체 영역 탐지 설정
YOLO_BOOK_MODEL_PATH = os.getenv(
    "YOLO_BOOK_MODEL_PATH",
    "models/yolov8n.pt",
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

@lru_cache(maxsize=1)
def get_book_detector() -> YOLO:
    """책 영역 탐지 모델의 최초 1회 로딩."""

    model_path = resolve_model_path(
        YOLO_BOOK_MODEL_PATH
    )

    if not model_path.is_file():
        raise FileNotFoundError(
            "책 영역 탐지 모델 파일이 없습니다: "
            f"{model_path}"
        )

    return YOLO(str(model_path))


def detect_book_region(
    image: Image.Image,
    image_index: int,
) -> dict:
    """사진에서 가장 큰 책의 전체 영역 탐지."""

    model = get_book_detector()

    book_class_ids = [
        int(class_id)
        for class_id, class_name
        in model.names.items()
        if normalize_model_class(
            str(class_name)
        ) == "book"
    ]

    if not book_class_ids:
        raise ValueError(
            "책 영역 탐지 모델에 book 클래스가 없습니다."
        )

    width, height = image.size

    result = model.predict(
        source=image,
        classes=book_class_ids,
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
            "source_model": "coco_book_detector",
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
        "source_model": "coco_book_detector",
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


def crop_to_book_region(
    image: Image.Image,
    book_region: dict,
) -> tuple[
    Image.Image | None,
    tuple[int, int, int, int] | None,
]:
    """검증된 책 영역 Crop 생성."""

    pixel_bbox = book_region.get("pixel_bbox")

    if (
        not book_region.get("usable")
        or pixel_bbox is None
    ):
        return None, None

    width, height = image.size
    x1, y1, x2, y2 = map(int, pixel_bbox)

    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))

    return (
        image.crop((x1, y1, x2, y2)),
        (x1, y1, x2, y2),
    )


def remap_yolo_coordinates(
    raw_detections: list[dict],
    candidates: list[dict],
    crop_box: tuple[int, int, int, int],
    original_size: tuple[int, int],
) -> None:
    """책 Crop 좌표를 원본 사진 좌표로 복원."""

    offset_x, offset_y, _, _ = crop_box
    original_width, original_height = (
        original_size
    )

    for item in [*raw_detections, *candidates]:
        x1, y1, x2, y2 = item["pixel_bbox"]

        x1 += offset_x
        x2 += offset_x
        y1 += offset_y
        y2 += offset_y

        item["pixel_bbox"] = [
            x1,
            y1,
            x2,
            y2,
        ]
        item["bbox"] = [
            round(x1 / original_width, 6),
            round(y1 / original_height, 6),
            round(x2 / original_width, 6),
            round(y2 / original_height, 6),
        ]


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


def _load_inspection_image(
    raw_path: str,
) -> Image.Image:
    """CloudFront URL 또는 로컬 경로의 검수 이미지 로드."""

    source = raw_path.strip()

    if not source:
        raise ValueError(
            "검수 이미지 경로가 비어 있습니다."
        )

    parsed_url = urlsplit(source)

    # 프론트에서 전달된 CloudFront HTTPS 이미지 처리
    if parsed_url.scheme in {"http", "https"}:
        # 실제 원격 이미지 검증 시점에만 백엔드 설정 로드
        from app.services.inspection_image_service import (
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

        with urlopen(
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

    # 기존 PowerShell 테스트용 로컬 이미지 처리
    else:
        image_path = Path(source)

        if not image_path.is_absolute():
            repo_root = (
                Path(__file__)
                .resolve()
                .parents[2]
            )
            image_path = (
                repo_root / image_path
            )

        if not image_path.exists():
            raise FileNotFoundError(
                "검수 이미지가 없습니다: "
                f"{image_path}"
            )

        image_source = image_path

    try:
        with Image.open(
            image_source
        ) as image:
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

            try:
                detected_region = detect_book_region(
                    image,
                    image_index,
                )
            except Exception as error:
                detected_region = {
                    "detected": False,
                    "confidence": None,
                    "bbox": None,
                    "pixel_bbox": None,
                    "area_ratio": 0.0,
                    "usable": False,
                    "error": f"{type(error).__name__}: {error}",
                }

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
                "detector_error": detected_region.get("error"),
            })
            book_regions.append(fallback_region)

    except Exception as error:
        message = (
            "책 영역 탐지 중 이미지를 처리할 수 없습니다: "
            f"{type(error).__name__}: {error}"
        )
        trace_event("BOOK_DETECTOR_FAILED", {"error": message})
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

        # 손글씨 모델은 속지 사진에만 적용
        # 표지 제목·그림·출판사 로고의 손글씨 오탐 차단
        if (
            spec.role == "DOODLE_SPECIALIST"
            and image_index != 2
        ):
            continue

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
            "book_regions": state.get("book_regions") or [],
            "raw_yolo_detections": [],
            "ensemble_candidates": [],
            "reviewed_candidates": [],
            "rejected_candidates": [],
            "uncertain_candidates": [],
            "missed_defect_suspected": False,
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

    if len(image_paths) != len(IMAGE_VIEWS):
        return failure_result(
            "앞면, 뒷면, 속지 이미지가 "
            "정확히 3장 필요합니다."
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

    prompt = """
당신은 중고 도서 검수 시스템의 Vision 2차 검증자입니다.

[역할]
- YOLO가 제안한 candidate_id와 BBox가 실제 도서 결함인지 검증합니다.
- YOLO 후보는 결함 확정값이 아니라 검토 대상입니다.
- 현재 사진 유형은 FRONT, BACK, INNER 중 하나입니다.

[금지 사항]
1. 새로운 candidate_id 또는 BBox를 만들지 않습니다.
2. 입력받은 BBox 좌표를 변경하지 않습니다.
3. is_mint, UBCI 점수, 등급, reason_code를 결정하지 않습니다.
4. 모든 candidate_id를 정확히 한 번씩 반환합니다.
5. BBox가 있다는 이유만으로 결함을 확정하지 않습니다.

[후보별 판정 순서]
1. BBox가 실제 책 표면 또는 책 모서리에 위치하는지 확인합니다.
2. 찢김, 섬유 단절, 눌림, 주름, 오염 확산, 코팅 손실,
   페이지 분리, 비정상 필기처럼 물리적 변화가 보이는지 확인합니다.
3. 표지 디자인, 인쇄물, 조명, 그림자, 반사, 배경 물체로
   동일한 모습을 설명할 수 있는지 확인합니다.

[정상 요소]
- 표지 제목, 로고, 삽화, 무늬, 띠지, 정상 스티커
- 코팅 반사, 플래시 반사, 그림자, 조명 명암
- 책상, 바닥, 다른 책 등 배경 물체
- 속지의 인쇄된 문제, 해설, 표, 선, 페이지 번호
- 정상적인 종이 질감과 미세한 사용 흔적

[결함 판정 근거]
- COVER_SCRATCH: 표면 코팅을 가로지르는 비정상 긁힘
- COVER_TEAR: 종이 섬유가 끊기거나 벌어진 찢김
- CORNER_CRUSH: 모서리 형태가 눌리거나 찌그러진 상태
- EDGE_WEAR: 책 가장자리의 국소적인 코팅 손실 또는 심한 마모
- GENERAL_STAIN: 인쇄가 아닌 불규칙한 색 번짐 또는 잔류 오염
- WATER_DAMAGE: 물결 자국, 테두리형 얼룩, 변색과 주름이 함께 보이는 상태
- PAGE_WARPING: 페이지 전체에 반복되는 물결 모양 변형
- PAGE_FOLD: 명확한 접힘선과 페이지 형상 변화
- LOOSE_BINDING: 페이지 벌어짐, 분리 또는 비정상적인 제본 틈
- FADING: 조명 명암이 아닌 넓은 범위의 불균일한 색 손실
- WRITING: 인쇄 글자와 굵기, 정렬, 색상 또는 형태가 다른 손글씨
- HIGHLIGHTING: 인쇄 내용 위에 추가된 형광펜 또는 표시선

[사진 유형별 주의 사항]
- FRONT/BACK:
  표지 글자, 삽화, 패턴, 코팅 반사를 결함으로 판단하지 않습니다.
- INNER:
  인쇄된 문제, 밑줄, 표, 해설을 필기로 판단하지 않습니다.
  인쇄 정렬과 다른 자유로운 획이 확인될 때만 WRITING을 확정합니다.

[결정]
- CONFIRMED:
  BBox 안에서 물리적 결함의 직접적인 시각 근거가 확인된 경우
- REJECTED:
  배경 물체, 표지 디자인, 조명·그림자 등 정상 요소인 경우
- UNCERTAIN:
  흐림, 가림, 근거 부족 또는 모델 클래스 충돌로 확정할 수 없는 경우

[필드 규칙]
- CONFIRMED:
  confirmed_type과 location이 필수이며 reject_reason은 null입니다.
- REJECTED:
  confirmed_type=null, location=null이며 정상 요소에 해당하는
  reject_reason을 반환합니다.
- UNCERTAIN:
  confirmed_type=null, location=null이며
  reject_reason=INSUFFICIENT_EVIDENCE를 반환합니다.
- review_confidence는 결함 가능성이 아니라 현재 판정의 확실성입니다.
- 최상위 review_confidence는 사진 품질과 후보 외 결함 검토를 포함한
  사진 전체 검토의 확실성입니다.
- review_confidence:
  0.90 이상은 직접적인 결함 근거가 명확한 경우,
  0.80~0.89는 결함이 보이지만 범위가 제한적인 경우,
  0.80 미만은 반드시 UNCERTAIN으로 반환합니다.
- missed_defect_suspected:
  모든 후보 BBox 밖에 명확한 추가 결함이 보일 때만 true로 반환합니다.
  단순한 의심만으로 true를 반환하지 않습니다.

[신뢰도 규칙]
- review_confidence는 결함 존재 가능성이 아니라 현재 판정의 확실성입니다.
- 정상 요소임이 명확한 REJECTED도 높은 신뢰도를 사용할 수 있습니다.
- YOLO confidence를 그대로 복사하지 않습니다.
- 흐림, 가림 또는 시각 근거 부족일 때만 낮은 신뢰도를 사용합니다.

[후보 종류 규칙]
- proposed_type은 YOLO가 제공한 참고 힌트일 뿐입니다.
- 직접 보이는 시각 근거가 다른 결함을 나타내면 confirmed_type을 올바른 결함으로 반환합니다.
- UNCERTAIN:
  흐림, 가림, 초점 불량 또는 시각 근거 부족으로 확정할 수 없는 경우

[후보가 없는 경우]
- reviews는 빈 배열로 반환합니다.
- 사진 품질과 후보 밖의 명확한 결함 존재 여부는 전체 이미지로 검토합니다.
- 명확한 추가 결함이 없다면 missed_defect_suspected=false로 반환합니다.
"""

    try:
        review_model = ChatOpenAI(
            model=os.getenv(
                "OPENAI_VISION_MODEL",
                os.getenv(
                    "OPENAI_MODEL",
                    "gpt-4o-mini",
                ),
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
        image_confidence_values: list[float] = []

        all_image_quality_ok = True
        missed_defect_suspected = False

        for (
            image_index,
            raw_path,
        ) in enumerate(image_paths):
            image = _load_inspection_image(raw_path)

            book_region = book_regions[image_index]

            analysis_image, crop_box = crop_to_book_region(
                image,
                book_region,
            )

            if analysis_image is None or crop_box is None:
                book_region["analysis_skipped"] = True
                all_image_quality_ok = False

                trace_event(
                    "BOOK_REGION_REJECTED",
                    book_region,
                )
                continue

            raw_detections, candidates = (
                detect_yolo_candidates(
                    analysis_image,
                    image_index,
                )
            )

            # 분석 이미지 좌표로 먼저 시각화
            annotated = draw_candidates(
                analysis_image,
                candidates,
            )

            # 저장·프론트 전달용 원본 좌표 복원
            remap_yolo_coordinates(
                raw_detections,
                candidates,
                crop_box,
                image.size,
            )

            book_region.update({
                "crop_applied": crop_box != (
                    0,
                    0,
                    image.width,
                    image.height,
                ),
                "analysis_skipped": False,
                "candidate_count_before": len(candidates),
                "candidate_count_after": len(candidates),
            })

            trace_event(
                "YOLO_ENSEMBLE_COMPLETED",
                {
                    "image_index": image_index,
                    "coordinate_space": "ORIGINAL_IMAGE",
                    "book_region": book_region,
                    "raw_detections": raw_detections,
                    "ensemble_candidates": [
                        state_safe_candidate(candidate)
                        for candidate in candidates
                    ],
                },
            )

            all_raw_detections.extend(
                raw_detections
            )

            all_ensemble_candidates.extend(
                state_safe_candidate(candidate)
                for candidate in candidates
            )

            candidate_metadata = [
                {
                    "image_view": candidate["image_view"],
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
                    f"현재 사진 유형: {IMAGE_VIEWS[image_index]}\n"
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
                        "url": image_to_data_url(
                        annotated,
                        max_side=1280,
                        quality=80,
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
                        f"{candidate['candidate_id']}입니다. "
                        f"사진 유형은 {candidate['image_view']}입니다. "
                        "BBox는 변경할 수 없습니다."
                    ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_to_data_url(
                            candidate["crop"],
                            max_side=512,
                            quality=75,
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

            if review.reviews:
                image_confidence_values.extend(
                    item.review_confidence
                    for item in review.reviews
                )
            else:
                image_confidence_values.append(
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

                defect = DefectOutput(
                    type=item.confirmed_type,
                    location=item.location,
                    bbox=candidate["bbox"],
                    ratio=calculate_bbox_area_ratio(
                    candidate["bbox"],
                    book_region["bbox"],
                    ),
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
                    "ratio_source": "BBOX_AREA",
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
            min(image_confidence_values)
            if image_confidence_values
            else 1.0
        )

    except Exception as error:
        error_type = type(error).__name__

        print(
            "[Agent] Hybrid Vision 실패:",
            error_type,
            str(error),
        )

        trace_event(
            "VISION_ERROR",
            {
                "error_type": error_type,
                "error_message": str(error),
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
                "YOLO 후보 밖에서 추가 결함이 의심됩니다. "
                "관리자 확인이 필요합니다."
            )
            revision_count += 1

    elif uncertain_candidates:
            vision_status = "REVIEW_REQUIRED"
            vision_reason_code = (
                "VISION_UNCERTAIN_CANDIDATE"
            )
            repair_directive = (
                "결함 여부를 확정하지 못한 후보가 있습니다. "
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
        # 승인된 결함이 없는 완료 결과의 MINT 판정
        "is_mint": (
            not final_defects
            if vision_status == "COMPLETED"
            else None
        ),
        "yolo_model_manifest": (
            model_manifest
        ),
        "book_regions": book_regions,
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
        "missed_defect_suspected": (
            missed_defect_suspected
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
                    f"결함확정={len(final_defects)}, "
                    "오탐제외="
                    f"{len(rejected_candidates)}, "
                    "판정보류="
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
            "book_regions": book_regions,
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
            "missed_defect_suspected": (
                missed_defect_suspected
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
    elif reason_code == "OK" and (type(rule_reference) is not str or not rule_reference.strip()):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "rule_reference는 비어 있지 않은 문자열이어야 합니다."

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
                    f"{reason_code} / "
                    f"{rag_result['critic_decision_source']}"
                )
            )
        ],
    }

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
        is_mint is not True
        or type(defects) is not list
        or defects
    ):
        raise ValueError(
            "Auto Refund는 결함 없는 MINT 도서만 "
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

    overall_confidence = float(vision_confidence)
    ubci_score = 100.0
    final_grade = "S"

    report = {
        "result": "AUTO_REFUND_APPROVED",
        "decision": "AI_FAST_TRACK",
        "is_mint": True,
        "defects": [],
        "ubci_score": ubci_score,
        "predicted_grade": final_grade,
        "final_grade": final_grade,
        "score_breakdown": [],
        "fatal_defect_detected": False,
        "grade_reason_code": "NO_VISIBLE_DEFECT",
        "rule_reference": POLICY_VERSION,
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
        "predicted_grade": final_grade,
        "score_breakdown": [],
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
                    "[Auto Refund Agent] "
                    "MINT 자동 승인 및 품질보증서 생성 완료"
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
