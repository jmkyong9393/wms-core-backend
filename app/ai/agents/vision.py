"""Vision Agent (GPT-4o + YOLO 하이브리드 판독)"""
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
from app.ai.agents.detector import *
from app.ai.agents.detector import _load_inspection_image
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
        from app.ai.instrumentation import token_collector

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
            callbacks=[token_collector],
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
            callbacks=[token_collector],
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
