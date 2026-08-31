"""APPROVE_NORMAL 판정의 final_grade 확정 규칙 검증.

Vision 단계에서 점수 없이 HITL로 직행한 건(ubci_score=None)을 관리자가 정상 승인하면
final_grade가 MINT로 확정되어야 WMS 재고 편입이 422 없이 통과한다.
"""

from app.models.wms import ReturnJob, ReturnJobStatus
from app.schemas.hitl import HITLAction, HITLReasonCode
from app.services.hitl_service import apply_hitl_final_decision


def _make_hitl_job(ubci_score=None) -> ReturnJob:
    return ReturnJob(
        status=ReturnJobStatus.HITL_REQUIRED,
        ubci_score=ubci_score,
        agent_logs={},
    )


def test_approve_normal_without_score_sets_mint_grade():
    job = _make_hitl_job(ubci_score=None)
    logs: dict = {}

    apply_hitl_final_decision(
        return_job=job,
        action=HITLAction.APPROVE_NORMAL,
        reviewer_reason_code=HITLReasonCode.FP_OTHER,
        target_grade=None,
        updated_logs=logs,
        task_id="task-1",
    )

    assert logs["final_grade"] == "MINT"
    assert logs["ai_decision"] == "APPROVE"
    assert job.status == ReturnJobStatus.PROCESSING


def test_approve_normal_with_score_keeps_grade_none():
    # 점수가 있으면 WMS가 UBCI 점수로 등급을 유도하므로 final_grade를 강제하지 않는다.
    job = _make_hitl_job(ubci_score=82.5)
    logs: dict = {}

    apply_hitl_final_decision(
        return_job=job,
        action=HITLAction.APPROVE_NORMAL,
        reviewer_reason_code=HITLReasonCode.FP_OTHER,
        target_grade=None,
        updated_logs=logs,
        task_id="task-2",
    )

    assert logs["final_grade"] is None
