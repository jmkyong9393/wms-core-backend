import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status

from sqlmodel import Session, select


from app.models.wms import (
    Book,
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
)


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

def get_inspection_history(
    session: Session,
    tenant_id: UUID,
    status: ReturnJobStatus | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    keyword: str | None = None,
) -> list[InspectionHistoryRow]:
    """
    해당 테넌트의 검수 이력을 조회한다.

    status:
        검수 상태 필터

    start_date / end_date:
        검수 요청 기간 필터

    keyword:
        도서명 검색 키워드

    TODO:
    현재는 프론트 Export를 위해 목록 데이터를 제공하는 구조이며,
    CSV/XLSX 생성은 프론트에서 처리한다.
    """
    statement = (
        select(
            ReturnJob,
            Book.title,
        )
        .join(
            Book,
            ReturnJob.book_id == Book.id,
        )
        .where(
            ReturnJob.tenant_id == tenant_id,
        )
        .order_by(
            ReturnJob.created_at.desc(),
        )
    )

    # 상태 필터
    if status:
        statement = statement.where(
            ReturnJob.status == status
        )


    # 날짜 필터
    if start_date:
        statement = statement.where(
            ReturnJob.created_at >= start_date
        )

    if end_date:
        statement = statement.where(
            ReturnJob.created_at <= end_date
        )

    # 도서명 검색 키워드 필터
    if keyword:
        statement = statement.where(
            Book.title.contains(keyword)
        )

    rows = session.exec(statement).all()

    inspection_history: list[InspectionHistoryRow] = []

    for return_job, book_title in rows:
        logs = return_job.agent_logs or {}

        inspection_history.append(
            InspectionHistoryRow(
                id=return_job.id,
                book_id=return_job.book_id,
                book_title=book_title,

                # UBCI 등급 산정 로직 연동 전까지 null
                final_grade=None,

                # AutoRefund Agent 실행 여부
                is_fast_track=(
                    logs.get("is_fast_track") is True
                ),

                status=return_job.status,
                ubci_score=return_job.ubci_score,
                final_report=_extract_final_report_summary(
                    return_job.final_report,
                ),
                reason_codes=_extract_reason_codes(
                    logs,
                ),
                # 프론트 검수 상세 모달용 Agent 실행 이력
                steps=_build_agent_steps(
                    logs.get("steps")
                ),
                inspected_at=return_job.created_at,
                updated_at=return_job.updated_at,
            )
        )

    return inspection_history

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
        final_grade=None,
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