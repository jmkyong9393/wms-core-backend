import json
import os
from uuid import uuid4

from PIL import Image

from app.ai import agents
from app.ai.agents import (
    CandidateReview,
    auto_refund_agent,
    book_detector_node,
    deduplicate_confirmed_defects,
    policy_agent,
)

import pytest

os.environ["LANGSMITH_TRACING"] = "false"

from app.ai.supervisor import (
    app_graph,
    resume_hitl,
    route_from_supervisor,
    technical_failure_node,
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
        "fatal_defect_detected": True,
        "grade_reason_code": "WATER_DAMAGE",
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


def test_failed_book_detector_routes_to_technical_failure():
    assert route_from_supervisor({
        "revision_count": 0,
        "book_regions": [],
        "repair_directive": "책 영역 탐지 실패",
    }) == "technical_failure_node"


def test_failed_vision_routes_to_technical_failure():
    assert route_from_supervisor({
        "revision_count": 1,
        "vision_status": "FAILED",
        "vision_reason_code": "QUALITY_ERROR",
        "repair_directive": (
            "FileNotFoundError: 검수 이미지를 찾을 수 없습니다."
        ),
    }) == "technical_failure_node"


def test_technical_failure_is_raised_for_worker_retry():
    with pytest.raises(
        RuntimeError,
        match="FileNotFoundError",
    ):
        technical_failure_node({
            "repair_directive": (
                "FileNotFoundError: 검수 이미지를 찾을 수 없습니다."
            ),
        })



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
        "vision_status": "COMPLETED",
        "image_quality_ok": True,
        "missed_defect_suspected": False,
        "uncertain_candidates": [],
        "human_feedback": None,
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
        "fatal_defect_detected": False,
        "grade_reason_code": "NO_VISIBLE_DEFECT",
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


def test_local_image_loader_requires_configured_root(
    monkeypatch,
    tmp_path,
):
    image_path = tmp_path / "book.png"
    Image.new("RGB", (10, 10)).save(image_path)
    monkeypatch.delenv(
        "INSPECTION_LOCAL_IMAGE_ROOT",
        raising=False,
    )

    with pytest.raises(
        ValueError,
        match="비활성화",
    ):
        agents._load_inspection_image(
            str(image_path)
        )


def test_local_image_loader_blocks_path_escape(
    monkeypatch,
    tmp_path,
):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside_image = tmp_path / "outside.png"
    Image.new("RGB", (10, 10)).save(outside_image)
    monkeypatch.setenv(
        "INSPECTION_LOCAL_IMAGE_ROOT",
        str(allowed_root),
    )

    with pytest.raises(
        ValueError,
        match="허용된 로컬",
    ):
        agents._load_inspection_image(
            str(outside_image)
        )


def test_local_image_loader_enforces_pixel_limit(
    monkeypatch,
    tmp_path,
):
    image_path = tmp_path / "book.png"
    Image.new("RGB", (20, 20)).save(image_path)
    monkeypatch.setenv(
        "INSPECTION_LOCAL_IMAGE_ROOT",
        str(tmp_path),
    )
    monkeypatch.setattr(
        agents,
        "MAX_INSPECTION_IMAGE_PIXELS",
        100,
    )

    with pytest.raises(
        ValueError,
        match="픽셀 수",
    ):
        agents._load_inspection_image(
            str(image_path)
        )


def test_book_detector_exception_is_not_hidden_by_fallback(
    monkeypatch,
):
    monkeypatch.setattr(
        agents,
        "_load_inspection_image",
        lambda _: Image.new("RGB", (100, 100)),
    )

    def fail_detector(*args, **kwargs):
        raise RuntimeError("missing detector dependency")

    monkeypatch.setattr(
        agents,
        "detect_book_region",
        fail_detector,
    )
    monkeypatch.setattr(
        agents,
        "trace_event",
        lambda *args, **kwargs: None,
    )

    result = book_detector_node({
        "image_paths": ["front", "back", "inner"],
    })

    assert result["book_regions"] == []
    assert result["repair_directive"] == (
        "책 영역 탐지 중 기술 오류가 발생했습니다."
    )


# 동일 사진의 중첩 결함만 하나로 합치는지 검증
def test_duplicate_confirmed_defects_are_deduplicated():
    defects = [
        {
            "image_index": 0,
            "candidate_id": 0,
            "type": "COVER_TEAR",
            "defect_type": "COVER_TEAR",
            "bbox": [
                0.574405,
                0.068122,
                0.634673,
                0.122024,
            ],
            "vlm_confidence": 0.90,
            "ensemble_confidence": 0.526927,
            "yolo_confidence": 0.526927,
        },
        {
            "image_index": 0,
            "candidate_id": 1,
            "type": "COVER_TEAR",
            "defect_type": "COVER_TEAR",
            "bbox": [
                0.581349,
                0.074074,
                0.677083,
                0.122354,
            ],
            "vlm_confidence": 0.90,
            "ensemble_confidence": 0.262395,
            "yolo_confidence": 0.262395,
        },
        {
            "image_index": 1,
            "candidate_id": 2,
            "type": "COVER_TEAR",
            "defect_type": "COVER_TEAR",
            "bbox": [
                0.267857,
                0.564153,
                0.291171,
                0.583003,
            ],
            "vlm_confidence": 0.90,
            "ensemble_confidence": 0.180838,
            "yolo_confidence": 0.180838,
        },
    ]

    result = deduplicate_confirmed_defects(
        defects
    )

    assert [
        item["candidate_id"]
        for item in result
    ] == [0, 2]


# 확정된 내지 훼손의 자동 감점 및 Critic/Report 라우팅 검증
def test_policy_scores_confirmed_inner_page_damage(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.ai.agents.search_policy_rules",
        lambda **_: [],
    )

    vision_state = {
        "vision_status": "COMPLETED",
        "image_quality_ok": True,
        "yolo_model_manifest": [],
        "raw_yolo_detections": [],
        "ensemble_candidates": [],
        "reviewed_candidates": [],
        "rejected_candidates": [],
        "uncertain_candidates": [],
        "revision_count": 0,
        "vision_confidence": 0.95,
        "defects": [
            {
                "type": "CORNER_CRUSH",
                "ratio": 0.85,
                "text_overlap": False,
                "morphology_severe": False,
            },
            {
                "type": "WRITING",
                "ratio": 3.22,
                "text_overlap": False,
                "morphology_severe": False,
            },
        ],
    }

    result = policy_agent(vision_state)

    assert result["ubci_score"] == 87.0
    assert result["provisional_ubci_score"] is None
    assert result["predicted_grade"] == "A"
    assert result["score_breakdown"] == [
        {
            "type": "CORNER_CRUSH",
            "total_ratio": 0.85,
            "severity": "MINOR",
            "text_overlap": False,
            "applied_penalty": 3.0,
            "fatal": False,
        },
        {
            "type": "INNER_PAGE_DAMAGE",
            "detected_types": ["WRITING"],
            "total_ratio": 3.22,
            "severity": "OBSERVED_LE_5_PAGES",
            "text_overlap": False,
            "applied_penalty": 10.0,
            "fatal": False,
        },
    ]
    assert result["provisional_score_breakdown"] is None
    assert result["reason_code"] is None
    policy_message = result["messages"][-1].content
    assert (
        "감점=CORNER_CRUSH -3.0점, "
        "INNER_PAGE_DAMAGE -10.0점"
        in policy_message
    )
    assert "Policy RAG=RULE_ENGINE_FALLBACK" in policy_message
    assert "근거=0건" in policy_message

    policy_state = {
        **vision_state,
        **result,
    }
    assert route_from_supervisor(policy_state) == "critic_agent"
    assert route_from_supervisor({
        **policy_state,
        "reason_code": "OK",
    }) == "report_agent"


def test_inner_page_damage_is_penalized_only_once():
    score, breakdown, fatal = agents.calculate_ubci_score([
        {
            "type": defect_type,
            "ratio": 4.0,
            "text_overlap": False,
            "morphology_severe": False,
        }
        for defect_type in (
            "WRITING",
            "HIGHLIGHTING",
            "PAGE_FOLD",
        )
    ])

    assert score == 90.0
    assert fatal is False
    assert breakdown == [{
        "type": "INNER_PAGE_DAMAGE",
        "detected_types": [
            "HIGHLIGHTING",
            "PAGE_FOLD",
            "WRITING",
        ],
        "total_ratio": 12.0,
        "severity": "OBSERVED_LE_5_PAGES",
        "text_overlap": False,
        "applied_penalty": 10.0,
        "fatal": False,
    }]


def test_policy_unknown_penalty_still_requires_hitl(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.ai.agents.search_policy_rules",
        lambda **_: [],
    )

    result = policy_agent({
        "vision_status": "COMPLETED",
        "revision_count": 0,
        "defects": [
            {
                "type": "CORNER_CRUSH",
                "ratio": 0.85,
                "text_overlap": False,
                "morphology_severe": False,
            },
            {
                "type": "BARCODE_DAMAGE",
                "ratio": 3.22,
                "text_overlap": False,
                "morphology_severe": False,
            },
        ],
    })

    assert result["ubci_score"] is None
    assert result["provisional_ubci_score"] == 97.0
    assert result["predicted_grade"] is None
    assert result["reason_code"] == "POLICY_REQUIRES_HITL"


# 관리자 확인 결함이 치명 결함의 즉시 반려를 가리지 않는지 검증
def test_fatal_defect_takes_priority_over_manual_defect(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.ai.agents.search_policy_rules",
        lambda **_: [],
    )

    result = policy_agent({
        "vision_status": "COMPLETED",
        "revision_count": 0,
        "defects": [
            {
                "type": "WATER_DAMAGE",
                "ratio": 1.0,
                "text_overlap": False,
                "morphology_severe": False,
            },
            {
                "type": "WRITING",
                "ratio": 3.22,
                "text_overlap": False,
                "morphology_severe": False,
            },
        ],
    })

    assert result["ubci_score"] == 0.0
    assert result["provisional_ubci_score"] is None
    assert result["predicted_grade"] == "REJECT"
    assert result["fatal_defect_detected"] is True
    assert result["grade_reason_code"] == "WATER_DAMAGE"
    assert result["reason_code"] is None


# 같은 물리 영역의 일반 결함보다 구체 결함을 우선하는지 검증
def test_specific_defect_replaces_overlapping_generic_defect():
    result = deduplicate_confirmed_defects([
        {
            "image_index": 0,
            "candidate_id": 0,
            "type": "OTHER_VISIBLE_DAMAGE",
            "defect_type": "OTHER_VISIBLE_DAMAGE",
            "bbox": [0.10, 0.10, 0.30, 0.30],
            "vlm_confidence": 0.99,
            "ensemble_confidence": 0.90,
            "yolo_confidence": 0.90,
        },
        {
            "image_index": 0,
            "candidate_id": 1,
            "type": "COVER_TEAR",
            "defect_type": "COVER_TEAR",
            "bbox": [0.11, 0.11, 0.31, 0.31],
            "vlm_confidence": 0.85,
            "ensemble_confidence": 0.80,
            "yolo_confidence": 0.80,
        },
    ])

    assert len(result) == 1
    assert result[0]["candidate_id"] == 1
    assert result[0]["type"] == "COVER_TEAR"


def test_two_track_vision_uses_full_vlm_only_for_inner(
    monkeypatch,
):
    model_calls = []
    invoke_counts = {"full": 0, "combined": 0}
    full_request_texts = []

    class FakeChatOpenAI:
        def __init__(self, *, model, **kwargs):
            self.model = model
            self.schema = None
            model_calls.append(model)

        def with_structured_output(
            self,
            schema,
            method=None,
        ):
            self.schema = schema
            return self

        def invoke(self, messages):
            if self.schema is agents.FullImageVisionReview:
                invoke_counts["full"] += 1
                full_request_texts.extend(
                    block["text"]
                    for block in messages[-1].content
                    if block.get("type") == "text"
                )
                return agents.FullImageVisionReview(
                    image_quality_ok=True,
                    defects=[
                        agents.DefectOutput(
                            type="WRITING",
                            location="INNER_PAGE",
                            bbox=[0.1, 0.1, 0.2, 0.2],
                            ratio=1.0,
                            confidence=0.96,
                            image_index=2,
                            observation="인쇄된 악보 기호",
                        )
                    ],
                    observations=["속지 전체 판독 완료"],
                    review_confidence=0.96,
                )

            invoke_counts["combined"] += 1
            return agents.CombinedVisionReview(
                image_quality_ok=True,
                reviews=[
                    agents.CombinedDefectReview(
                        defect_index=index,
                        decision="CONFIRMED",
                        review_confidence=0.95,
                        explanation="원본 증거와 일치",
                        printed_content_only=(
                            index == 2
                        ),
                    )
                    for index in range(3)
                ],
                missed_defect_suspected=False,
                review_confidence=0.95,
            )

    def fake_candidates(image, image_index, book_region):
        defect_type = (
            "WRITING"
            if image_index == 2
            else "COVER_TEAR"
        )
        candidate = {
            "candidate_id": 0,
            "image_view": agents.IMAGE_VIEWS[image_index],
            "image_index": image_index,
            "bbox": [0.1, 0.1, 0.2, 0.2],
            "coordinate_space": "ORIGINAL_IMAGE_NORMALIZED",
            "book_coverage": 1.0,
            "book_center_inside": True,
            "pixel_bbox": [10, 10, 20, 20],
            "proposed_type": defect_type,
            "yolo_confidence": 0.9,
            "ensemble_confidence": 0.9,
            "source_models": ["test-model"],
            "source_predictions": [],
            "class_conflict": False,
            "crop": Image.new("RGB", (32, 32)),
        }
        return [], [candidate]

    monkeypatch.setattr(
        agents,
        "ChatOpenAI",
        FakeChatOpenAI,
    )
    monkeypatch.setattr(
        agents,
        "get_yolo_model_manifest",
        lambda: [
            {
                "name": "general_binary",
                "path": (
                    "/app/models/"
                    "general_binary_team_s3_v2_best.pt"
                ),
            },
            {
                "name": "doodle",
                "path": "/app/models/doodle_best.pt",
            },
        ],
    )
    monkeypatch.setattr(
        agents,
        "_load_inspection_image",
        lambda path: Image.new("RGB", (100, 100)),
    )
    monkeypatch.setattr(
        agents,
        "detect_yolo_candidates",
        fake_candidates,
    )
    monkeypatch.setattr(
        agents,
        "trace_event",
        lambda *args, **kwargs: None,
    )

    result = agents.vision_agent({
        "image_paths": ["front", "back", "inner"],
        "book_regions": [
            {
                "image_index": index,
                "image_view": view,
                "bbox": [0.0, 0.0, 1.0, 1.0],
                "fallback_used": False,
            }
            for index, view in enumerate(
                agents.IMAGE_VIEWS
            )
        ],
        "revision_count": 0,
    })

    assert model_calls == ["gpt-4o", "gpt-4o-mini"]
    assert invoke_counts == {"full": 1, "combined": 1}
    full_request_text = "\n".join(full_request_texts)
    assert '"candidate_id": 0' in full_request_text
    assert '"proposed_type"' not in full_request_text
    assert result["vision_status"] == "COMPLETED"
    assert [
        region["analysis_mode"]
        for region in result["book_regions"]
    ] == [
        "TRAINED_DETERMINISTIC_YOLO",
        "TRAINED_DETERMINISTIC_YOLO",
        "UNTRAINED_FULL_IMAGE_VLM",
    ]
    assert len(result["defects"]) == 2
    assert len(result["rejected_candidates"]) == 1
    assert all(
        "image_url" not in defect
        and "source_predictions" not in defect
        for defect in result["defects"]
    )
    assert all(
        "source_predictions" not in candidate
        for candidate in result["ensemble_candidates"]
    )
    assert all(
        "image_url" not in candidate
        and "source_predictions" not in candidate
        for candidate in result["rejected_candidates"]
    )
    assert result["vision_observations"] == [
        {
            "image_index": 2,
            "image_view": "INNER",
            "observation": "속지 전체 판독 완료",
            "source": "FULL_IMAGE_GPT4O",
        }
    ]
    vision_message = result["messages"][-1].content
    assert "결함=COVER_TEAR×2" in vision_message
    assert "WRITING" not in vision_message
    assert (
        "general_binary="
        "general_binary_team_s3_v2_best.pt"
        in vision_message
    )
    assert "doodle=doodle_best.pt" in vision_message
    assert "INNER→Doodle+GPT-4o" in vision_message


def test_mint_routes_to_auto_refund_after_critic():
    assert route_from_supervisor({
        "revision_count": 0,
        "is_mint": True,
        "defects": [],
        "vision_confidence": 0.95,
        "ubci_score": 100.0,
        "predicted_grade": "S",
        "score_breakdown": [],
        "fatal_defect_detected": False,
        "grade_reason_code": "NO_VISIBLE_DEFECT",
        "rule_reference": "UBCI_SPEC_V2.0.0.0",
        "policy_confidence": 1.0,
        "reason_code": "OK",
    }) == "auto_refund_agent"
