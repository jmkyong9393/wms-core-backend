"""Policy Agent (UBCI 감점 매트릭스 + RAG 근거)"""
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
