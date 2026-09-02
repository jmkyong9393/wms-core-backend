"""APPROVE_NORMAL 판정의 final_grade 확정 규칙을 검증한다."""

from app.models.wms import ReturnJob, ReturnJobStatus
from app.domains.inspections.schemas.hitl import HITLAction, HITLReasonCode
from app.domains.inspections.hitl_service import apply_hitl_final_decision


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
    # 점수가 있으면 WMS가 등급을 유도하므로 final_grade를 강제하지 않는다.
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
