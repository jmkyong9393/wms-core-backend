import json
import os
from uuid import uuid4

from app.ai.agents import (
    CandidateReview,
    auto_refund_agent,
)

import pytest

os.environ["LANGSMITH_TRACING"] = "false"

from app.ai.supervisor import (
    app_graph,
    resume_hitl,
    route_from_supervisor,
)


# HITL 테스트용 검수 상태
def make_hitl_state() -> dict:
    return {
        "messages": [],
        "tenant_id": "TEST",
        "is_mint": False,
        "defects": [
            {
                "type": "WATER_DAMAGE",
                "location": "INNER_PAGE",
                "ratio": 8.2,
                "confidence": 0.93,
            }
        ],
        "vision_confidence": 0.93,
        "ubci_score": 72.5,
        "predicted_grade": "B",
        "score_breakdown": [
            {
                "type": "WATER_DAMAGE",
                "applied_penalty": 27.5,
            }
        ],
        "rule_reference": "UBCI_SPEC_V2.0.0.0",
        "policy_confidence": 0.91,
        "reason_code": "QUALITY_ERROR",
        "revision_count": 2,
        "human_feedback": None,
        "final_report": None,
    }


# 고유 MemorySaver 설정
def make_config() -> tuple[str, dict]:
    thread_id = f"hitl-{uuid4()}"
    return (
        thread_id,
        {
            "configurable": {
                "thread_id": thread_id,
            }
        },
    )


# MemorySaver Pause 검증
def test_graph_pauses_before_human_node():
    _, config = make_config()

    app_graph.invoke(
        make_hitl_state(),
        config=config,
    )
    snapshot = app_graph.get_state(config)

    assert snapshot.next == ("human_node",)
    assert snapshot.values["final_report"] is None


# 하향 승인 Resume 및 보증서 데이터 검증
def test_resume_downgrade_creates_certificate():
    thread_id, config = make_config()
    app_graph.invoke(
        make_hitl_state(),
        config=config,
    )

    result = resume_hitl(
        thread_id=thread_id,
        human_feedback="APPROVE_DOWNGRADE",
        primary_reason_code="DMG_EXT_WET",
        target_grade="NORMAL",
    )
    report = json.loads(result["final_report"])

    assert result["target_grade"] == "B"
    assert result["final_grade"] == "B"
    assert result["human_feedback"] is None

    report = json.loads(result["final_report"])

    assert report["decision"] == "APPROVE_DOWNGRADE"
    assert report["predicted_grade"] == "B"
    assert report["final_grade"] == "B"
    assert report["primary_reason_code"] == "DMG_EXT_WET"


# 정상 승인 감점 제거 및 MINT 보증서 검증
def test_resume_normal_approval_creates_mint_certificate():
    thread_id, config = make_config()
    app_graph.invoke(
        make_hitl_state(),
        config=config,
    )

    result = resume_hitl(
        thread_id=thread_id,
        human_feedback="APPROVE_NORMAL",
        primary_reason_code="FP_SHADOW",
    )

    report = json.loads(result["final_report"])

    assert result["ubci_score"] == 72.5
    assert result["predicted_grade"] == "B"
    assert result["final_grade"] == "S"

    assert report["ubci_score"] == 72.5
    assert report["predicted_grade"] == "B"
    assert report["final_grade"] == "S"



# 관리자 반려 결과 검증
def test_resume_rejection_sets_reject_grade():
    thread_id, config = make_config()

    app_graph.invoke(
        make_hitl_state(),
        config=config,
    )

    result = resume_hitl(
        thread_id=thread_id,
        human_feedback="REJECT_RETURN",
        primary_reason_code="DMG_EXT_WET",
    )
    report = json.loads(result["final_report"])

    assert app_graph.get_state(config).next == ()
    assert result["final_grade"] == "REJECT"
    assert report["decision"] == "REJECT_RETURN"
    assert report["final_grade"] == "REJECT"
    assert report["primary_reason_code"] == "DMG_EXT_WET"


# 잘못된 하향 등급 차단 검증
def test_resume_rejects_invalid_target_grade():
    thread_id, config = make_config()
    app_graph.invoke(
        make_hitl_state(),
        config=config,
    )

    with pytest.raises(ValueError):
        resume_hitl(
            thread_id=thread_id,
            human_feedback="APPROVE_DOWNGRADE",
            primary_reason_code="DMG_EXT_WET",
            target_grade="S",
        )

    assert app_graph.get_state(config).next == ("human_node",)


# 존재하지 않는 체크포인트 차단 검증
def test_resume_rejects_unknown_thread():
    with pytest.raises(ValueError):
        resume_hitl(
            thread_id=f"missing-{uuid4()}",
            human_feedback="APPROVE_NORMAL",
            primary_reason_code="FP_OTHER",
        )


# 재촬영 결정의 Vision 재진입 검증
def test_recheck_routes_to_book_detector():
    assert route_from_supervisor({
        "human_feedback": "RE_CHECK",
        "revision_count": 0,
    }) == "book_detector"

def test_initial_inspection_routes_to_book_detector():
    assert route_from_supervisor({
        "revision_count": 0,
    }) == "book_detector"


def test_completed_book_detector_routes_to_vision():
    assert route_from_supervisor({
        "revision_count": 0,
        "book_regions": [
            {"image_index": 0},
            {"image_index": 1},
            {"image_index": 2},
        ],
    }) == "vision_agent"


def test_failed_book_detector_routes_to_human():
    assert route_from_supervisor({
        "revision_count": 0,
        "book_regions": [],
        "repair_directive": "책 영역 탐지 실패",
    }) == "human_node"



# 정의되지 않은 관리자 사유 차단 검증
def test_resume_rejects_invalid_reason_code():
    thread_id, config = make_config()

    app_graph.invoke(
        make_hitl_state(),
        config=config,
    )

    with pytest.raises(ValueError):
        resume_hitl(
            thread_id=thread_id,
            human_feedback="APPROVE_NORMAL",
            primary_reason_code="UNKNOWN_REASON",
        )

    assert app_graph.get_state(config).next == (
        "human_node",
    )

# MINT 자동 환불 승인 사유서 검증
def test_fast_track_creates_refund_report():
    result = auto_refund_agent({
        "is_mint": True,
        "defects": [],
        "vision_confidence": 0.95,
    })
    report = json.loads(result["final_report"])

    assert report["result"] == "AUTO_REFUND_APPROVED"
    assert report["decision"] == "AI_FAST_TRACK"
    assert report["is_mint"] is True
    assert report["defects"] == []
    assert report["vision_confidence"] == 0.95
    assert report["overall_confidence"] == 0.95
    assert "MINT 자동 승인" in report["message"]
    assert report["ubci_score"] == 100.0
    assert report["predicted_grade"] == "S"
    assert report["final_grade"] == "S"

    with pytest.raises(ValueError):
        auto_refund_agent({
            "is_mint": False,
            "defects": [
                {
                    "type": "WATER_DAMAGE",
                }
            ],
            "vision_confidence": 0.95,
        })


# Policy 처리 이후 Auto Refund 재진입 차단 검증
def test_policy_result_does_not_route_to_auto_refund():
    assert route_from_supervisor({
        "revision_count": 0,
        "is_mint": True,
        "defects": [],
        "vision_confidence": 0.95,
        "ubci_score": 100.0,
        "predicted_grade": "S",
        "score_breakdown": [],
        "rule_reference": "UBCI_SPEC_V2.0.0.0",
        "policy_confidence": 0.98,
        "reason_code": None,
    }) == "critic_agent"


# 재촬영 후 MINT 결과의 Auto Refund 진입 차단 검증
def test_rechecked_mint_does_not_route_to_auto_refund():
    assert route_from_supervisor({
        "revision_count": 0,
        "is_mint": True,
        "defects": [],
        "vision_confidence": 0.95,
        "primary_reason_code": "SYS_BLURRY",
        "reason_code": None,
    }) == "policy_agent"


# 재촬영 검증 완료 후 관리자 재확인 경로 검증
def test_rechecked_result_returns_to_human():
    assert route_from_supervisor({
        "revision_count": 0,
        "is_mint": True,
        "defects": [],
        "vision_confidence": 0.95,
        "ubci_score": 100.0,
        "predicted_grade": "S",
        "rule_reference": "UBCI_SPEC_V2.0.0.0",
        "policy_confidence": 0.98,
        "reason_code": "OK",
        "primary_reason_code": "SYS_BLURRY",
        "human_feedback": None,
    }) == "human_node"


# REVIEW_REQUIRED 결과의 Vision 재호출 차단 검증
def test_review_required_routes_directly_to_human():
    assert route_from_supervisor({
        "revision_count": 1,
        "vision_status": "REVIEW_REQUIRED",
        "vision_reason_code": "VISION_UNCERTAIN_CANDIDATE",
        "is_mint": None,
        "defects": [],
        "vision_confidence": 0.85,
    }) == "human_node"


# 낮은 신뢰도의 확정 후보를 판정보류로 전환하는지 검증
def test_low_confidence_candidate_becomes_uncertain():
    review = CandidateReview(
        candidate_id=0,
        decision="CONFIRMED",
        confirmed_type="COVER_TEAR",
        location="FRONT_COVER",
        ratio=3.0,
        review_confidence=0.79,
    )

    assert review.decision == "UNCERTAIN"
    assert review.confirmed_type is None
    assert review.location is None
    assert (
        review.reject_reason
        == "INSUFFICIENT_EVIDENCE"
    )
