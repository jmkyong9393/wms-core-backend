import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status

from sqlalchemy import func
from sqlmodel import Session, select


from app.models.wms import (
    Book,
    ConditionGrade,
    ReturnJob,
    ReturnJobStatus,
)
from app.schemas.admin_inspection import (
    AgentLogStep,
    HITLHistoryItem,
    InspectionAIResult,
    InspectionBookDetail,
    InspectionDetailResponse,
    InspectionErrorDetail,
    InspectionHistoryRow,
    InspectionHistoryListResponse,
)

VALID_FINAL_GRADES = {
    "MINT",
    "EXCELLENT",
    "NORMAL",
    "REJECT",
}


def _extract_final_grade(
    agent_logs: dict | None,
) -> str | None:
    value = (agent_logs or {}).get("final_grade")

    if value in VALID_FINAL_GRADES:
        return value

    return None

def _resolve_final_grade(
    condition_grade: ConditionGrade | None,
    agent_logs: dict | None,
) -> str | None:
    """
    목록 표시와 필터의 등급 기준을 맞춘다.

    최근 검수 건은 ReturnJob.condition_grade를 확정 등급으로 사용하고,
    해당 값이 없는 과거 데이터만 agent_logs의 final_grade를 보조로 사용한다.
    """
    if condition_grade is not None:
        return condition_grade.value

    return _extract_final_grade(agent_logs)

def _extract_final_report_summary(
    final_report: str | None,
) -> str | None:
    """
    목록 그리드에 표시할 최종 리포트 요약을 반환한다.

    final_report가 JSON 문자열이면 message 또는 result를 사용하고,
    일반 문자열이면 그대로 반환한다.
    """
    if final_report is None:
        return None

    normalized_report = final_report.strip()

    if not normalized_report:
        return None

    try:
        parsed_report = json.loads(normalized_report)
    except (json.JSONDecodeError, TypeError):
        return normalized_report

    if not isinstance(parsed_report, dict):
        return normalized_report

    message = parsed_report.get("message")

    if isinstance(message, str) and message.strip():
        return message.strip()

    result = parsed_report.get("result")

    if isinstance(result, str) and result.strip():
        return result.strip()

    return normalized_report

# 문자열 값을 중복 없이 목록에 추가한다.
def _append_unique(
    target: list[str],
    value: object,
) -> None:
    if not isinstance(value, str):
        return

    normalized_value = value.strip()

    if not normalized_value:
        return

    if normalized_value not in target:
        target.append(normalized_value)


def _extract_reason_codes(
    agent_logs: dict | None,
) -> list[str]:
    """
    그리드에 표시할 대표 사유 코드를 추출한다.

    - AI reason_code가 OK가 아니면 포함
    - 최신 HITL 관리자 사유 코드를 포함
    - 동일한 코드는 중복 제거
    """
    logs = agent_logs or {}
    reason_codes: list[str] = []

    ai_reason_code = logs.get("reason_code")

    if ai_reason_code != "OK":
        _append_unique(
            reason_codes,
            ai_reason_code,
        )

    hitl_history = logs.get("hitl_history")

    if isinstance(hitl_history, list) and hitl_history:
        latest_hitl = hitl_history[-1]

        if isinstance(latest_hitl, dict):
            _append_unique(
                reason_codes,
                latest_hitl.get("reviewer_reason_code"),
            )

    return reason_codes

# step 변환
def _build_agent_steps(
    raw_steps: object,
) -> list[AgentLogStep]:
    if not isinstance(raw_steps, list):
        return []

    steps: list[AgentLogStep] = []

    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue

        step_order = raw_step.get("step_order")
        agent_name = raw_step.get("agent_name")
        execution_status = raw_step.get(
            "execution_status"
        )
        result_summary = raw_step.get(
            "result_summary"
        )

        if not isinstance(step_order, int):
            continue

        if not isinstance(agent_name, str):
            continue

        if not isinstance(execution_status, str):
            continue

        if not isinstance(result_summary, str):
            continue

        steps.append(
            AgentLogStep(
                step_order=step_order,
                agent_name=agent_name,
                execution_status=execution_status,
                result_summary=result_summary,
                reasoning=raw_step.get("reasoning"),
                reason_code=raw_step.get("reason_code"),
            )
        )

    return steps

def _apply_inspection_history_filters(
    statement,
    tenant_id: UUID,
    status: ReturnJobStatus | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    keyword: str | None = None,
    grade: ConditionGrade | None = None,
    fast_track: bool | None = None,
    reason_code: str | None = None,
):
    """
    검수 이력 목록과 전체 건수 조회에 공통으로 적용할 필터를 구성한다.

    목록 조회와 COUNT 조회가 서로 다른 필터를 사용하면
    total 값과 실제 items 수가 달라질 수 있으므로 공통 함수로 관리한다.
    """
    statement = statement.where(
        ReturnJob.tenant_id == tenant_id,
    )

    if status is not None:
        statement = statement.where(
            ReturnJob.status == status,
        )

    if start_date is not None:
        statement = statement.where(
            ReturnJob.created_at >= start_date,
        )

    if end_date is not None:
        statement = statement.where(
            ReturnJob.created_at <= end_date,
        )

    normalized_keyword = (keyword or "").strip()

    if normalized_keyword:
        statement = statement.where(
            Book.title.contains(normalized_keyword),
        )

    if grade is not None:
        statement = statement.where(
            ReturnJob.condition_grade == grade,
        )

    if fast_track is not None:
        statement = statement.where(
            func.coalesce(
                ReturnJob.agent_logs["is_fast_track"]
                .as_boolean(),
                False,
            )
            == fast_track,
        )

    normalized_reason_code = (reason_code or "").strip()

    if normalized_reason_code:
        statement = statement.where(
            ReturnJob.agent_logs["reason_code"]
            .as_string()
            == normalized_reason_code,
        )

    return statement


def get_inspection_history(
    session: Session,
    tenant_id: UUID,
    status: ReturnJobStatus | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    keyword: str | None = None,
    grade: ConditionGrade | None = None,
    fast_track: bool | None = None,
    reason_code: str | None = None,
    page: int = 1,
    size: int = 20,
) -> InspectionHistoryListResponse:
    """
    테넌트별 검수 이력을 서버 페이지네이션 방식으로 조회한다.

    필터가 적용된 전체 건수(total)를 먼저 조회한 뒤,
    같은 필터 조건으로 현재 페이지에 필요한 데이터만 DB에서 가져온다.
    """
    count_statement = (
        select(func.count())
        .select_from(ReturnJob)
        .join(
            Book,
            ReturnJob.book_id == Book.id,
        )
    )

    count_statement = _apply_inspection_history_filters(
        statement=count_statement,
        tenant_id=tenant_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        grade=grade,
        fast_track=fast_track,
        reason_code=reason_code,
    )

    total = session.exec(count_statement).one()

    offset = (page - 1) * size

    history_statement = (
        select(
            ReturnJob,
            Book.title,
        )
        .join(
            Book,
            ReturnJob.book_id == Book.id,
        )
    )

    history_statement = _apply_inspection_history_filters(
        statement=history_statement,
        tenant_id=tenant_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        grade=grade,
        fast_track=fast_track,
        reason_code=reason_code,
    )

    rows = session.exec(
        history_statement
        .order_by(
            ReturnJob.created_at.desc(),
            ReturnJob.id.desc(),
        )
        .offset(offset)
        .limit(size)
    ).all()

    inspection_history: list[InspectionHistoryRow] = []

    for return_job, book_title in rows:
        logs = return_job.agent_logs or {}

        inspection_history.append(
            InspectionHistoryRow(
                id=return_job.id,
                book_id=return_job.book_id,
                book_title=book_title,
                final_grade=_resolve_final_grade(
                    condition_grade=getattr(
                        return_job,
                        "condition_grade",
                        None,
                    ),
                    agent_logs=logs,
                ),
                is_fast_track=(
                    logs.get("is_fast_track") is True
                ),
                status=return_job.status,
                ubci_score=return_job.ubci_score,
                final_report=_extract_final_report_summary(
                    return_job.final_report,
                ),
                reason_codes=_extract_reason_codes(logs),
                steps=_build_agent_steps(
                    logs.get("steps"),
                ),
                inspected_at=return_job.created_at,
                updated_at=return_job.updated_at,
            )
        )

    total_pages = (
        (total + size - 1) // size
        if total > 0
        else 0
    )

    return InspectionHistoryListResponse(
        items=inspection_history,
        total=total,
        page=page,
        size=size,
        total_pages=total_pages,
    )

def _parse_final_report(
    final_report: str | None,
) -> dict | None:
    if final_report is None:
        return None

    normalized_report = final_report.strip()

    if not normalized_report:
        return None

    try:
        parsed_report = json.loads(normalized_report)
    except (json.JSONDecodeError, TypeError):
        return None

    if isinstance(parsed_report, dict):
        return parsed_report

    return None


def _build_error_detail(
    error_data: object,
) -> InspectionErrorDetail | None:
    if not isinstance(error_data, dict):
        return None

    return InspectionErrorDetail(
        type=error_data.get("type"),
        message=error_data.get("message"),
        task_id=error_data.get("task_id"),
        failed_at=error_data.get("failed_at"),
    )


def _build_hitl_history(
    hitl_history: object,
) -> list[HITLHistoryItem]:
    if not isinstance(hitl_history, list):
        return []

    history_items: list[HITLHistoryItem] = []

    for history in hitl_history:
        if not isinstance(history, dict):
            continue

        history_items.append(
            HITLHistoryItem(
                action=history.get("action"),
                reviewer_reason_code=history.get(
                    "reviewer_reason_code"
                ),
                target_grade=history.get("target_grade"),
                comment=history.get("comment"),
                reviewer_id=history.get("reviewer_id"),
                reviewer_employee_id=history.get(
                    "reviewer_employee_id"
                ),
                reviewed_at=history.get("reviewed_at"),
                task_id=history.get("task_id"),
            )
        )

    return history_items

def get_inspection_agent_logs(
    session: Session,
    tenant_id: UUID,
    job_id: UUID,
) -> list[AgentLogStep]:
    return_job = session.exec(
        select(ReturnJob).where(
            ReturnJob.id == job_id,
            ReturnJob.tenant_id == tenant_id,
        )
    ).first()

    if return_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="검수 작업을 찾을 수 없습니다.",
        )

    logs = return_job.agent_logs or {}

    return _build_agent_steps(logs.get("steps"))

def get_inspection_detail(
    session: Session,
    tenant_id: UUID,
    job_id: UUID,
) -> InspectionDetailResponse:
    statement = (
        select(
            ReturnJob,
            Book.title,
            Book.isbn,
        )
        .join(
            Book,
            ReturnJob.book_id == Book.id,
        )
        .where(
            ReturnJob.id == job_id,
            ReturnJob.tenant_id == tenant_id,
        )
    )

    row = session.exec(statement).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="검수 작업을 찾을 수 없습니다.",
        )

    return_job, book_title, book_isbn = row

    logs = return_job.agent_logs or {}
    parsed_report = _parse_final_report(
        return_job.final_report
    )

    defects = logs.get("defects")

    if not isinstance(defects, list):
        defects = []

    # agent_logs에 값이 없을 경우 final_report JSON을 보조적으로 사용
    if not defects and parsed_report:
        report_defects = parsed_report.get("defects")

        if isinstance(report_defects, list):
            defects = report_defects

    ai_reason_code = logs.get("reason_code")

    if ai_reason_code is None and parsed_report:
        ai_reason_code = parsed_report.get("reason_code")

    return InspectionDetailResponse(
        id=return_job.id,
        book=InspectionBookDetail(
            id=return_job.book_id,
            title=book_title,
            isbn=book_isbn,
        ),
        status=return_job.status,
        mode=return_job.mode.value,
        final_grade=_extract_final_grade(logs),
        is_fast_track=(
            (return_job.agent_logs or {}).get(
                "is_fast_track"
            ) is True
        ),
        ubci_score=return_job.ubci_score,
        final_report=_extract_final_report_summary(
            return_job.final_report
        ),
        original_image_urls=[
            str(image_path)
            for image_path in (return_job.image_paths or [])
        ],
        ai_result=InspectionAIResult(
            decision=logs.get("ai_decision"),
            reason_code=ai_reason_code,
            defects=defects,
            revision_count=(
                logs.get("revision_count")
                if isinstance(logs.get("revision_count"), int)
                else 0
            ),
            repair_directive=logs.get(
                "repair_directive"
            ),
        ),
        hitl=(
            logs.get("hitl")
            if isinstance(logs.get("hitl"), dict)
            else {}
        ),
        hitl_history=_build_hitl_history(
            logs.get("hitl_history")
        ),
        error=_build_error_detail(
            logs.get("error")
        ),
        wms_error=_build_error_detail(
            logs.get("wms_error")
        ),
        steps=_build_agent_steps(
            logs.get("steps")
        ),
        inspected_at=return_job.created_at,
        updated_at=return_job.updated_at,
    )