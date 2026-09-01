"""YOLO 로딩·책 영역 탐지·후보 융합 등 결정론적 탐지 계층"""
import base64
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ultralytics import YOLO

from app.ai.agents.common import *
from app.ai.agents.schemas import *

from ..rag.critic_cases import (
    CRITIC_PROMPT_VERSION,
    evaluate_with_precedents,
)
from ..rag.policy_search import (
    UBCI_POLICY_VERSION,
    search_policy_rules,
)
from ..state import Grade, WMSInspectionState

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
