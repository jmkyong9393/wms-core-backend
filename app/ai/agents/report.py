"""Report·AutoRefund Agent"""
# ruff: noqa: F401,F403
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
from ..rag.critic_cases import (
    CRITIC_PROMPT_VERSION,
    evaluate_with_precedents,
)
from ..rag.policy_search import (
    UBCI_POLICY_VERSION,
    search_policy_rules,
)

from ..state import Grade, WMSInspectionState


from app.ai.agents.common import *  # noqa: F401,F403
from app.ai.agents.schemas import *  # noqa: F401,F403
from app.ai.agents.policy import *  # noqa: F401,F403
from app.ai.agents.critic import _public_policy_evidence  # noqa: F401



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
