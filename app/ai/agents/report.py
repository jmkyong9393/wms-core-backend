"""Report·AutoRefund Agent"""
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
from app.ai.agents.critic import _public_policy_evidence
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



# 고객 노출용 결함 한글 라벨 (내부 코드 비노출 원칙)
DEFECT_LABEL_KO = {
    "COVER_SCRATCH": "표지 긁힘",
    "COVER_TEAR": "표지 찢어짐",
    "STICKER_MARK": "스티커 자국",
    "CORNER_CRUSH": "모서리 눌림",
    "EDGE_WEAR": "책 가장자리 마모",
    "SPINE_CRACKING": "책등 갈라짐",
    "LOOSE_BINDING": "제본 느슨함",
    "GENERAL_STAIN": "오염",
    "FADING": "변색",
    "SIGNATURE": "서명 흔적",
    "LIBRARY_STAMP": "도서관 직인",
    "WATER_DAMAGE": "물 얼룩",
    "PAGE_WARPING": "페이지 휘어짐",
    "PAGE_FOLD": "페이지 접힘",
    "WRITING": "필기 흔적",
    "HIGHLIGHTING": "형광펜 표시",
    "BARCODE_DAMAGE": "바코드 훼손",
    "OTHER_VISIBLE_DAMAGE": "기타 외관 손상",
}

GRADE_LABEL_KO = {
    "S": "S등급 (최상)",
    "A": "A등급 (우수)",
    "B": "B등급 (양호)",
    "REJECT": "매입 불가",
}

# 고객 노출용 결함 위치 한글 라벨. 영문 enum을 그대로 프롬프트에 넣으면
# LLM이 그 코드를 고객 문구에 그대로 복사한다(실측).
LOCATION_LABEL_KO = {
    "FRONT_COVER": "앞표지",
    "BACK_COVER": "뒤표지",
    "SPINE": "책등",
    "CORNER": "모서리",
    "BOOK_EDGE": "책 가장자리",
    "INNER_PAGE": "내지",
    "IDENTIFIER_AREA": "바코드·식별 영역",
    "OTHER": "기타 부위",
}


def _location_label(value) -> str:
    return LOCATION_LABEL_KO.get(str(value or ""), "위치 미상")


def _fallback_narrative(
    final_grade: str,
    defects: list,
) -> dict:
    """LLM 호출 불가 시 사용하는 결정론적 보증서 서술.

    문장은 백엔드에서만 만든다 — 프론트가 등급별 문구를 하드코딩하지 않게 하기 위함.
    """
    notes = []
    for defect in defects or []:
        if not isinstance(defect, dict):
            continue
        label = DEFECT_LABEL_KO.get(
            str(defect.get("type") or ""),
            "외관 상태 참고사항",
        )
        notes.append(
            f"{label} ({_location_label(defect.get('location'))})"
        )

    grade_label = GRADE_LABEL_KO.get(final_grade, final_grade)
    if final_grade == "REJECT":
        message = (
            "정밀 검수 결과 재판매 기준을 충족하지 못해 "
            "매입이 어려운 상태로 확인되었습니다. 양해 부탁드립니다."
        )
    elif notes:
        message = (
            f"정밀 검수 결과 {grade_label}으로 판정되었습니다. "
            "아래 확인된 사항 외의 사용감은 발견되지 않았습니다."
        )
    else:
        message = (
            f"정밀 검수 결과 {grade_label}으로 판정되었습니다. "
            "눈에 띄는 결함 없이 양호한 상태입니다."
        )

    return {
        "customer_message": message,
        "condition_notes": notes,
        "narrative_source": "FALLBACK_RULE",
    }


def build_customer_narrative(
    final_grade: str,
    defects: list,
    ubci_score,
) -> dict:
    """GPT-4o-mini로 고객 공개용 보증서 서술을 생성한다. 실패 시 결정론적 폴백.

    [고객 노출 경계] 귀책을 단정하지 않고 내부 코드·조항 전문을 노출하지 않는다.
    """
    if os.getenv(
        "REPORT_NARRATIVE_LLM_ENABLED", "true"
    ).lower() in ("0", "false"):
        return _fallback_narrative(final_grade, defects)

    defect_lines = [
        f"- {DEFECT_LABEL_KO.get(str(d.get('type') or ''), '기타')} "
        f"(위치: {_location_label(d.get('location'))})"
        for d in (defects or [])
        if isinstance(d, dict)
    ]

    defect_block = (
        chr(10).join(defect_lines) if defect_lines else "- 없음"
    )
    prompt = f"""당신은 중고서점 품질보증서를 작성하는 CS 담당자입니다.
규칙:
1. 존댓말, 과장·추측 금지. 결함의 심각도에 맞는 어조 (가벼운 사용감은 다정하게, 매입 불가는 정중하되 단호하게).
2. 누구의 잘못인지(귀책)를 절대 쓰지 않는다.
3. 내부 코드명이나 규정 조항 전문을 쓰지 않는다.
4. 영문 대문자 코드(FRONT_COVER 등)를 그대로 옮겨 적지 말고 한글 표현만 사용한다.
판정 등급: {GRADE_LABEL_KO.get(final_grade, final_grade)}
품질 점수: {ubci_score if ubci_score is not None else '해당 없음'}
확인된 결함:
{defect_block}"""

    try:
        from langchain_openai import ChatOpenAI

        from app.ai.instrumentation import token_collector

        narrative_model = ChatOpenAI(
            # 프리즈 규정: Report Agent 모델은 gpt-4o-mini 고정
            model="gpt-4o-mini",
            temperature=0,
            timeout=30,
            max_retries=1,
            callbacks=[token_collector],
        ).with_structured_output(
            CustomerCertificateNarrative,
        )

        narrative = CustomerCertificateNarrative.model_validate(
            narrative_model.invoke(prompt)
        )
        return {
            "customer_message": narrative.customer_message,
            "condition_notes": narrative.condition_notes,
            "narrative_source": "LLM",
        }
    except Exception as error:
        print(
            "[Report Agent] 보증서 서술 LLM 실패 - "
            f"결정론적 폴백 사용 ({type(error).__name__})"
        )
        return _fallback_narrative(final_grade, defects)



def report_agent(state: WMSInspectionState) -> WMSInspectionState:
    """5. Report Agent — 판정 결과를 고객용 품질보증서로 렌더링한다.

    판정값(등급·점수·근거)은 결정론적으로 조립하고, 고객이 읽는 문장만
    build_customer_narrative가 GPT-4o-mini로 생성한다(실패 시 규칙 폴백).
    출력: final_report (JSON 문자열)
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

    # 고객 공개용 서술 생성 (LLM + 결정론적 폴백).
    # 이 노드는 Celery 워커의 그래프 실행 안에서만 돌므로 API 동기 루프에서 LLM을 부르지 않는다.
    customer_narrative = build_customer_narrative(
        final_grade=final_grade,
        defects=state.get("defects") or [],
        ubci_score=state.get("ubci_score"),
    )

    report = {
        "result": result,
        "customer_narrative": customer_narrative,
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
