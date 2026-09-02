"""Critic Agent (교차 검증 + 판례 RAG)"""

import base64
import json
import logging
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
from app.ai.agents.policy import *
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

logger = logging.getLogger(__name__)


def critic_agent(state: WMSInspectionState) -> WMSInspectionState:
    """3. Critic Agent — Vision 결과와 Policy 감점 연산의 정합성을 교차 검증한다.

    검증에 실패하면 Policy로 되돌리고, 반복 실패하면 HITL로 넘긴다.
    출력: reason_code, revision_count
    """
    logger.info("[Agent] Critic Agent 실행...")

    raw_revision_count = state.get(
        "revision_count",
        0,
    )
    revision_count_is_valid = type(raw_revision_count) is int and raw_revision_count >= 0
    revision_count = raw_revision_count if revision_count_is_valid else 0

    is_mint = state.get("is_mint")
    defects = state.get("defects")
    vision_confidence = state.get("vision_confidence")
    ubci_score = state.get("ubci_score")
    predicted_grade = state.get("predicted_grade")
    score_breakdown = state.get("score_breakdown")
    fatal_defect_detected = state.get("fatal_defect_detected")
    rule_reference = state.get("rule_reference")
    policy_confidence = state.get("policy_confidence")

    reason_code = "OK"
    repair_directive = None
    overall_confidence = None

    # revision_count 타입 검증
    if not revision_count_is_valid:
        reason_code = "QUALITY_ERROR"
        repair_directive = "revision_count는 0 이상의 정수여야 합니다."

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
    elif reason_code == "OK" and (type(ubci_score) not in (int, float) or not 0 <= ubci_score <= 100):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "ubci_score는 0~100 범위의 숫자여야 합니다."

    # 정책 근거 검증
    elif reason_code == "OK" and rule_reference != POLICY_VERSION:
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "현재 서버 정책 버전과 rule_reference가 일치해야 합니다."

    # Policy 신뢰도 검증
    elif reason_code == "OK" and (type(policy_confidence) not in (int, float) or not 0 <= policy_confidence <= 1):
        reason_code = "UBCI_POLICY_VIOLATION"
        repair_directive = "policy_confidence는 0~1 범위의 숫자여야 합니다."

    elif reason_code == "OK" and policy_confidence < MIN_POLICY_CONFIDENCE:
        reason_code = "POLICY_LOW_CONFIDENCE"
        repair_directive = "Policy 검색 및 계산 신뢰도가 기준보다 낮습니다."

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
                repair_directive = "Policy 점수·등급·감점 내역이 결함 원본의 독립 재계산 결과와 일치하지 않습니다."

    # RAG 미실행 기본 결과
    rag_result = {
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "critic_rag_used": False,
        "critic_retrieved_case_ids": [],
        "critic_retrieval_scores": [],
        "critic_retrieval_count": 0,
        "critic_decision_source": "RULE_ONLY",
        "critic_explanation": ("규칙 검증에서 오류가 발견되어 판례 검색을 실행하지 않았습니다."),
        "critic_rag_confidence": None,
        "critic_prompt_version": (CRITIC_PROMPT_VERSION),
    }

    if reason_code == "OK":
        overall_confidence = min(
            vision_confidence,
            policy_confidence,
        )

        # 기본 규칙 통과 후 판례 RAG 실행
        rag_result = evaluate_with_precedents(state)

        if rag_result["reason_code"] != "OK":
            reason_code = rag_result["reason_code"]
            repair_directive = rag_result["repair_directive"]
            overall_confidence = None
            revision_count += 1

    else:
        revision_count += 1

    return {
        **rag_result,
        "reason_code": reason_code,
        "repair_directive": repair_directive,
        "revision_count": revision_count,
        "overall_confidence": (overall_confidence),
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
                if rule_id
                in {
                    "UBCI_POLICY",
                    "WMS_OPERATION_POLICY",
                }
                else "POLICY_REFERENCE"
            )

        safe_clause_ref = clause_ref.strip()
        chunk_id = item.get("chunk_id")

        if safe_clause_ref == chunk_id or re.fullmatch(
            r"(?:UBCI|WMS_OPERATION)_\d+",
            safe_clause_ref,
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

    return [
        {
            "policy_version": (state.get("rule_reference") or POLICY_VERSION),
            "rule_id": fallback_rule_id,
            "clause_ref": fallback_clause_ref,
            "source": fallback_source,
        }
    ]
