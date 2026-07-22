from uuid import UUID
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.exceptions import (
    HITLJobNotFoundException,
    InvalidHITLStateException,
)
from app.models.wms import ReturnJob, ReturnJobStatus, User
from app.schemas.hitl import HITLAction



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
    reviewer_reason_code: str | None,
    comment: str | None,
    task_id: str,
) -> dict[str, Any]:
    return {
        "action": action,
        "reviewer_id": str(reviewer_id),
        "reviewer_employee_id": reviewer_employee_id,
        "reviewer_reason_code": reviewer_reason_code,
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
    action: str,
    updated_logs: dict[str, Any],
    task_id: str,
) -> ReturnJob:
    if action not in {"APPROVE", "REJECT"}:
        raise ValueError(
            f"최종 HITL 판단으로 처리할 수 없는 action입니다: {action}"
        )

    # 기존 WMS 처리 함수가 읽는 관리자 최종 판단값
    updated_logs["ai_decision"] = action
    updated_logs["ai_completed"] = True

    # 최종 판단이 AI가 아닌 ADMIN의 HITL 판단임을 기록
    updated_logs["decision_source"] = "ADMIN_HITL"

    # 현재 HITL 검토가 완료되었음을 별도 로그로 표시
    updated_logs["hitl"] = {
        "required": False,
        "resolved": True,
        "action": action,
        "wms_task_id": task_id,
    }

    return_job.agent_logs = updated_logs

    # 관리자 판단은 끝났지만 실제 WMS 호출은 아직 실행 전이므로 PROCESSING
    return_job.status = ReturnJobStatus.PROCESSING

    # 실제로 실행될 WMS 후속 Celery Task ID로 교체
    return_job.task_id = task_id

    return_job.updated_at = datetime.utcnow()

    return return_job


# 관리자 재검수 요청을 저장하고 새로운 AI 검수 작업을 받을 수 있는 상태로 초기화
def apply_hitl_recalculate_decision(
        *,
        return_job: ReturnJob,
        updated_logs: dict[str, Any],
        task_id: str,
) -> ReturnJob:
    # 이전 ai 최종 판값이 새 검수에 사용되지 않도록 제거
    updated_logs.pop("ai_decision", None)
    updated_logs.pop("ai_completed", None)

    # 이전 최종 판단 출처가 새 AI 재검수 결과와 섞이지 않도록 제거
    updated_logs.pop("decision_source", None)

    # HITL 검토는 완료됐으며 새 AI 검수 작업이 등록될 예정임을 기록
    updated_logs["hitl"] = {
        "required": False,
        "resolved": True,
        "action": "RECALCULATE",
        "recalculation_task_id": task_id,
    }

    return_job.agent_logs = updated_logs

    # 새로운 AI 검수를 처음부터 실행하므로 대기 상태로 변경
    return_job.status = ReturnJobStatus.PENDING

    # 새로 등록할 process_inspection Celelry Task의 ID로 교체
    return_job.task_id = task_id

    # 이전 AI 검수 결과가 조회 API에 노출되지 않도록 초기화
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
    reviewer_reason_code: str | None,
    comment: str | None,
    task_id: str,
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
        "ubci_score": return_job.ubci_score,
        "final_report": return_job.final_report,
        "agent_logs": previous_agent_logs,
    }

    # 4. 누가 어떤 판단을 했는지 ADMIN 판단 로그 생성
    decision_log = build_hitl_decision_log(
        action=action.value,
        reviewer_id=current_admin.id,
        reviewer_employee_id=current_admin.employee_id,
        reviewer_reason_code=reviewer_reason_code,
        comment=comment,
        task_id=task_id,
    )

    # 5. 기존 AI 로그를 유지하면서 HITL 판단 이력 추가
    updated_logs = append_hitl_history(
        agent_logs=previous_agent_logs,
        decision_log=decision_log,
    )

    # 후속 Celery 작업 등록 실패 시 사용할 복구 데이터
    updated_logs["hitl_dispatch_backup"] = previous_job_data

    # 6. 판단 종류에 따라 WMS 처리 또는 AI 재검수 상태로 변경
    if action in {
        HITLAction.APPROVE,
        HITLAction.REJECT,
    }:
        apply_hitl_final_decision(
            return_job=return_job,
            action=action.value,
            updated_logs=updated_logs,
            task_id=task_id,
        )

    elif action == HITLAction.RECALCULATE:
        apply_hitl_recalculate_decision(
            return_job=return_job,
            updated_logs=updated_logs,
            task_id=task_id,
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