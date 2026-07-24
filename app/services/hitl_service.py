from uuid import UUID
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.exceptions import (
    HITLJobNotFoundException,
    InvalidHITLStateException,
)
from app.models.wms import ReturnJob, ReturnJobStatus, User
from app.schemas.hitl import (
    HITLAction,
    HITLReasonCode,
    HITLTargetGrade,
)



def save_return_job(
    session: Session,
    return_job: ReturnJob,
) -> None:
    session.add(return_job)
    session.commit()
    session.refresh(return_job)


# 관리자 판단 대상 ReturnJob을 Tenant 기준으로 조회하고 행 잠금을 획득
def get_hitl_job_for_update(
    session: Session,
    job_id: UUID,
    tenant_id: UUID,
) -> ReturnJob:
    statement = (
        select(ReturnJob)
        .where(
            ReturnJob.id == job_id,
            ReturnJob.tenant_id == tenant_id,
        )
        .with_for_update()
    )

    return_job = session.exec(statement).first()

    if return_job is None:
        raise HITLJobNotFoundException()

    return return_job


# 관리자 판단이 가능한 HITL_REQUIRED 상태인지 확인
def validate_hitl_required_status(
    return_job: ReturnJob,
) -> None:
    if return_job.status != ReturnJobStatus.HITL_REQUIRED:
        current_status = (
            return_job.status.value
            if hasattr(return_job.status, "value")
            else str(return_job.status)
        )

        raise InvalidHITLStateException(
            current_status=current_status,
        )
    
    
# 관리자 HITL 판단 내용을 DB 로그에 저장할 수 있는 형태로 생성
def build_hitl_decision_log(
    *,
    action: str,
    reviewer_id: UUID,
    reviewer_employee_id: str,
    reviewer_reason_code: str,
    target_grade: str | None,
    comment: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    return {
        "action": action,
        "reviewer_id": str(reviewer_id),
        "reviewer_employee_id": reviewer_employee_id,
        "reviewer_reason_code": reviewer_reason_code,
        "target_grade": target_grade,
        "comment": comment,
        "task_id": task_id,
        "reviewed_at": datetime.utcnow().isoformat(),
    }


# 기존 agent_Logs에 관리자 HITL 판단 이력을 누적
def append_hitl_history(
    agent_logs: dict[str, Any] | None,
    decision_log: dict[str, Any],
) -> dict[str, Any]:
    updated_logs = dict(agent_logs or {})

    hitl_history = list(
        updated_logs.get("hitl_history") or []
    )

    hitl_history.append(decision_log)

    updated_logs["hitl_history"] = hitl_history

    return updated_logs


# 관리자 APPROVE/REJECT 판단을 ReturnJob에 저장하고 WMS 처리 대기 상태로 변경
def apply_hitl_final_decision(
    *,
    return_job: ReturnJob,
    action: HITLAction,
    reviewer_reason_code: HITLReasonCode,
    target_grade: HITLTargetGrade | None,
    updated_logs: dict[str, Any],
    task_id: str,
) -> ReturnJob:
    approval_actions = {
        HITLAction.APPROVE_NORMAL,
        HITLAction.APPROVE_DOWNGRADE,
    }

    rejection_actions = {
        HITLAction.REJECT_RETURN,
        HITLAction.REJECT_DISCARD,
    }

    if action in approval_actions:
        wms_decision = "APPROVE"

    elif action in rejection_actions:
        wms_decision = "REJECT"

    else:
        raise ValueError(
            f"최종 HITL 판단으로 처리할 수 없는 action입니다: {action}"
        )

    if action == HITLAction.APPROVE_NORMAL:
        # TODO:
        # AI 검수 결과에 final_grade가 포함되면 해당 값을 그대로 승인한다.
        # 현재 AI Agent에서 final_grade를 생성하지 않으므로 None일 수 있다.
        final_grade = updated_logs.get("final_grade")

    elif action == HITLAction.APPROVE_DOWNGRADE:
        final_grade = (
            target_grade.value
            if target_grade is not None
            else None
        )

    elif action in rejection_actions:
        final_grade = "REJECT"

    else:
        final_grade = None

    rejection_disposition = None

    if action == HITLAction.REJECT_RETURN:
        rejection_disposition = "REJECT_RETURN"
    elif action == HITLAction.REJECT_DISCARD:
        rejection_disposition = "REJECT_DISCARD"

    # 기존 WMS 처리 함수와 호환되는 값
    updated_logs["ai_decision"] = wms_decision
    updated_logs["ai_completed"] = True
    updated_logs["wms_task_id"] = task_id

    # 관리자 세부 결정 정보
    updated_logs["admin_decision_code"] = reviewer_reason_code.value
    updated_logs["final_grade"] = final_grade
    updated_logs["rejection_disposition"] = rejection_disposition
    updated_logs["decision_source"] = "ADMIN_HITL"

    updated_logs["hitl"] = {
        "required": False,
        "resolved": True,
        "action": action.value,
        "wms_task_id": task_id,
    }

    return_job.agent_logs = updated_logs
    return_job.status = ReturnJobStatus.PROCESSING
    return_job.task_id = task_id
    return_job.updated_at = datetime.utcnow()

    return return_job


# 관리자 재검수 요청을 저장하고 새로운 AI 검수 작업을 받을 수 있는 상태로 초기화
def apply_hitl_recheck_decision(
    *,
    return_job: ReturnJob,
    updated_logs: dict[str, Any],
) -> ReturnJob:
    updated_logs.pop("ai_decision", None)
    updated_logs.pop("ai_completed", None)
    updated_logs.pop("inspection_task_id", None)
    updated_logs.pop("wms_task_id", None)
    updated_logs.pop("decision_source", None)
    updated_logs.pop("admin_decision_code", None)
    updated_logs.pop("final_grade", None)
    updated_logs.pop("rejection_disposition", None)

    # 이전 검수의 실패 정보가 새 재검수 결과로 노출되지 않도록 제거
    updated_logs.pop("error", None)
    updated_logs.pop("wms_error", None)
    updated_logs.pop("wms_dispatch_failed", None)
    updated_logs.pop("wms_dispatch_error", None)
    updated_logs.pop("wms_dispatch_failed_at", None)
    updated_logs.pop("failure_stage", None)
    updated_logs.pop("inspection_dispatch_error", None)

    updated_logs["hitl"] = {
        "required": False,
        "resolved": True,
        "action": HITLAction.RE_CHECK.value,
        "waiting_for_new_images": True,
    }

    return_job.agent_logs = updated_logs

    # 새 이미지 등록 전까지 대기
    return_job.status = ReturnJobStatus.RECHECK_REQUIRED

    # 아직 실행할 Celery 작업이 없으므로 초기화
    return_job.task_id = None

    # 기존 결과가 새 검수 결과처럼 노출되지 않도록 초기화
    return_job.ubci_score = None
    return_job.final_report = None

    return_job.updated_at = datetime.utcnow()

    return return_job


# 관리자 HITL 판단을 검증하고 ReturnJob에 저장
def save_hitl_decision(
    *,
    session: Session,
    job_id: UUID,
    current_admin: User,
    action: HITLAction,
    reviewer_reason_code: HITLReasonCode,
    target_grade: HITLTargetGrade | None,
    comment: str | None,
    task_id: str | None,
) -> ReturnJob:
    # 1. 현재 관리자의 Tenant에 속한 검수 작업을 조회하고 행 잠금
    return_job = get_hitl_job_for_update(
        session=session,
        job_id=job_id,
        tenant_id=current_admin.tenant_id,
    )

    # 2. 관리자 판단이 가능한 HITL_REQUIRED 상태인지 확인
    validate_hitl_required_status(
        return_job=return_job,
    )

    # 3. Celery 등록 실패 시 복구할 수 있도록 변경 전 데이터 백업
    # 이전 HITL 처리에서 삭제되지 못한 임시 백업은 새로운 복구 백업 안에 중첩되지 않도록 제거한다.
    previous_agent_logs = dict(
        return_job.agent_logs or {}
    )
    previous_agent_logs.pop(
        "hitl_dispatch_backup",
        None,
    )

    previous_job_data = {
        "task_id": return_job.task_id,
        "status": (
            return_job.status.value
            if hasattr(return_job.status, "value")
            else str(return_job.status)
        ),
        "ubci_score": (
            float(return_job.ubci_score)
            if return_job.ubci_score is not None
            else None
        ),
        "final_report": return_job.final_report,
        "agent_logs": previous_agent_logs,
    }

    # 4. 누가 어떤 판단을 했는지 ADMIN 판단 로그 생성
    decision_log = build_hitl_decision_log(
        action=action.value,
        reviewer_id=current_admin.id,
        reviewer_employee_id=current_admin.employee_id,
        reviewer_reason_code=reviewer_reason_code.value,
        target_grade=(
            target_grade.value
            if target_grade is not None
            else None
        ),
        comment=comment,
        task_id=task_id,
    )

    # 5. 기존 AI 로그를 유지하면서 HITL 판단 이력 추가
    updated_logs = append_hitl_history(
        agent_logs=previous_agent_logs,
        decision_log=decision_log,
    )

    # 후속 Celery 작업 등록 실패 시 사용할 복구 데이터
    if task_id is not None:
        updated_logs["hitl_dispatch_backup"] = previous_job_data

    # 6. 판단 종류에 따라 WMS 처리 또는 AI 재검수 상태로 변경
    if action in {
        HITLAction.APPROVE_NORMAL,
        HITLAction.APPROVE_DOWNGRADE,
        HITLAction.REJECT_RETURN,
        HITLAction.REJECT_DISCARD,
    }:
        if task_id is None:
            raise ValueError(
                "WMS 후속 처리에는 task_id가 필요합니다."
            )

        apply_hitl_final_decision(
            return_job=return_job,
            action=action,
            reviewer_reason_code=reviewer_reason_code,
            target_grade=target_grade,
            updated_logs=updated_logs,
            task_id=task_id,
        )

    elif action == HITLAction.RE_CHECK:
        apply_hitl_recheck_decision(
            return_job=return_job,
            updated_logs=updated_logs,
        )

    else:
        raise ValueError(
            f"지원하지 않는 HITL 판단입니다: {action}"
        )

    # 7. 변경된 ReturnJob을 DB에 저장
    save_return_job(
        session=session,
        return_job=return_job,
    )

    return return_job


# 후속 Celery 작업 등록 실패 시 ReturnJob을 기존 HITL_REQUIRED 상태로 복구
def restore_hitl_after_dispatch_failure(
    *,
    session: Session,
    job_id: UUID,
    tenant_id: UUID,
    failed_task_id: str,
    error: Exception,
) -> ReturnJob:
    # 다른 Tenant의 작업을 수정하지 못하도록 Tenant 조건과 행 잠금 적용
    return_job = get_hitl_job_for_update(
        session=session,
        job_id=job_id,
        tenant_id=tenant_id,
    )

    current_logs = dict(return_job.agent_logs or {})
    backup = current_logs.get("hitl_dispatch_backup")

    if not isinstance(backup, dict):
        raise ValueError(
            "HITL 작업 복구에 필요한 백업 데이터를 찾을 수 없습니다."
        )

    if return_job.status != ReturnJobStatus.PROCESSING:
        raise ValueError(
            "HITL 후속 Task 실패 상태를 복구할 수 없는 상태입니다. "
            f"job_id={job_id} "
            f"current_status={return_job.status}"
        )

    if return_job.task_id != failed_task_id:
        raise ValueError(
            "복구 대상 HITL Task ID가 현재 ReturnJob의 Task ID와 "
            "일치하지 않습니다. "
            f"job_id={job_id} "
            f"current_task_id={return_job.task_id} "
            f"failed_task_id={failed_task_id}"
        )

    # 관리자 판단 전 HITL 상태의 데이터를 복구
    return_job.task_id = backup.get("task_id")
    return_job.status = ReturnJobStatus.HITL_REQUIRED
    return_job.ubci_score = backup.get("ubci_score")
    return_job.final_report = backup.get("final_report")

    previous_logs = dict(
        backup.get("agent_logs") or {}
    )

    # 후속 Task 등록 실패 정보는 복구된 로그에 남김
    previous_logs["hitl_dispatch_error"] = {
        "failed_task_id": failed_task_id,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "failed_at": datetime.utcnow().isoformat(),
    }

    return_job.agent_logs = previous_logs
    return_job.updated_at = datetime.utcnow()

    save_return_job(
        session=session,
        return_job=return_job,
    )

    return return_job

# 후속 Celery 작업 등록 성공 후 임시 복구 백업 데이터를 제거
def clear_hitl_dispatch_backup(
    *,
    session: Session,
    job_id: UUID,
    tenant_id: UUID,
) -> ReturnJob:
    # 다른 Tenant의 작업을 수정하지 못하도록 Tenant 조건과 행 잠금 적용
    return_job = get_hitl_job_for_update(
        session=session,
        job_id=job_id,
        tenant_id=tenant_id,
    )

    updated_logs = dict(return_job.agent_logs or {})

    # Task 등록 실패에 대비해 임시 저장했던 백업 제거
    updated_logs.pop(
        "hitl_dispatch_backup",
        None,
    )

    return_job.agent_logs = updated_logs
    return_job.updated_at = datetime.utcnow()

    save_return_job(
        session=session,
        return_job=return_job,
    )

    return return_job