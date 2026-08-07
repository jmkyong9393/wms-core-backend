from datetime import datetime
from uuid import UUID
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlmodel import Session, select

from app.core.database import get_session
from app.api.dependencies.auth import require_admin_or_master
from app.models.wms import (
    ConditionGrade,
    ReturnJob,
    ReturnJobStatus,
    User,
    WeeklyInsight,
    FdsReport,
    FdsPolicy,
    Order,
)
from app.schemas.admin_inspection import (
    AgentLogStep,
    HITLQueueListResponse,
    InspectionDetailResponse,
    InspectionHistoryListResponse,
    HITLQueueMetricsResponse,
)
from app.schemas.admin_dashboard import (
    WeeklyInsightResponse,
    FdsReportResponse,
    FdsPolicyResponse,
    FdsPolicyUpdateRequest,
    OutboundDashboardSummaryResponse,
    DashboardFlowTrendResponse,
    InboundDashboardSummaryResponse,
)
from app.schemas.hitl import HITLQueueBucket
from app.services.admin_inspection_service import (
    get_inspection_agent_logs,
    get_inspection_detail,
    get_inspection_history,
    get_hitl_queue,
    get_hitl_queue_metrics,
)
from app.services.outbound_dashboard_service import (
    get_outbound_dashboard_summary as get_outbound_dashboard_summary_service,
)
from app.services.dashboard_flow_trend_service import (
    get_dashboard_flow_trend as get_dashboard_flow_trend_service,
)
from app.services.inbound_dashboard_service import (
    get_inbound_dashboard_summary
    as get_inbound_dashboard_summary_service,
)

router = APIRouter()

# 관리자용 검수 현황 통계 API
@router.get("/inspection-metrics")
def get_inspection_metrics(
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
):
    statement = select(ReturnJob).where(
        ReturnJob.tenant_id == current_admin.tenant_id,
    )
    jobs = session.exec(statement).all()

    total_jobs = len(jobs)

    pending_jobs = 0
    processing_jobs = 0
    approved_jobs = 0
    rejected_jobs = 0
    failed_jobs = 0
    hitl_required_jobs = 0
    recheck_required_jobs = 0

    processing_times = []

    for job in jobs:
        if job.status == ReturnJobStatus.PENDING:
            pending_jobs += 1

        elif job.status == ReturnJobStatus.PROCESSING:
            processing_jobs += 1

        elif job.status == ReturnJobStatus.HITL_REQUIRED:
            hitl_required_jobs += 1

        elif job.status == ReturnJobStatus.RECHECK_REQUIRED:
            recheck_required_jobs += 1

        elif job.status == ReturnJobStatus.APPROVED:
            approved_jobs += 1

        elif job.status == ReturnJobStatus.REJECTED:
            rejected_jobs += 1

        elif job.status == ReturnJobStatus.FAILED:
            failed_jobs += 1


        if job.ai_inspection_completed_at is not None:
            processing_time = max(
                0.0,
                (
                    job.ai_inspection_completed_at
                    - job.ai_inspection_started_at
                ).total_seconds(),
            )

            processing_times.append(processing_time)

    if processing_times:
        average_processing_time = (
            sum(processing_times) / len(processing_times)
        )
    else:
        average_processing_time = 0

    return {
        "total_jobs": total_jobs,
        "pending_jobs": pending_jobs,
        "processing_jobs": processing_jobs,
        "hitl_required_jobs": hitl_required_jobs,
        "recheck_required_jobs": recheck_required_jobs,
        "approved_jobs": approved_jobs,
        "rejected_jobs": rejected_jobs,
        "failed_jobs": failed_jobs,
        "average_processing_time_seconds": average_processing_time,
    }


@router.get(
    "/dashboard/outbound-summary",
    response_model=OutboundDashboardSummaryResponse,
    operation_id="getOutboundDashboardSummary",
    summary="출고 통합 대시보드 요약 조회",
    description=(
        "진행 중인 B2B 피킹 주문 수, 실제 바코드 스캔 기준 피킹 완료율, "
        "당일 송장 발급 완료 건수와 최근 출고 주문 목록을 반환합니다. "
        "AUTO_PO 입고 주문은 출고 대시보드 집계에서 제외합니다."
    ),
)
def get_outbound_dashboard_summary(
    _: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> OutboundDashboardSummaryResponse:
    return get_outbound_dashboard_summary_service(session)

@router.get(
    "/dashboard/inbound-summary",
    response_model=InboundDashboardSummaryResponse,
    operation_id="getInboundDashboardSummary",
    summary="입고 통합 대시보드 요약 조회",
    description=(
        "금일 입고 수량, 검수 처리 현황, 최근 입고 추이, "
        "중고·반품 검수 등급 분포, 구역별 가용 재고 및 최근 입고 내역을 반환합니다."
    ),
)
def get_inbound_dashboard_summary(
    days: int = Query(
        default=7,
        ge=1,
        le=31,
        description="입고 추이 조회 기간(일)",
    ),
    _: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> InboundDashboardSummaryResponse:
    return get_inbound_dashboard_summary_service(
        session=session,
        days=days,
    )


@router.get(
    "/dashboard/flow-trend",
    response_model=DashboardFlowTrendResponse,
    operation_id="getDashboardFlowTrend",
    summary="입출고 및 AI 검수 처리 시간 일별 추이 조회",
    description=(
        "최근 지정 기간의 일별 입고·출고 수량과 "
        "완료된 AI 검수 건의 평균 처리 시간을 반환합니다."
    ),
)
def get_dashboard_flow_trend(
    days: int = Query(
        default=7,
        ge=1,
        le=31,
        description="조회할 최근 일수",
    ),
    _: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> DashboardFlowTrendResponse:
    return get_dashboard_flow_trend_service(
        session=session,
        days=days,
    )


# 관리자용 전체 검수 이력 그리드 조회 API
@router.get(
    "/inspections",
    response_model=InspectionHistoryListResponse,
)
def get_admin_inspection_history(
    status: ReturnJobStatus | None = Query(
        default=None,
        description="검수 상태 필터(APPROVED, FAILED 등)",
    ),
    start_date: datetime | None = Query(
        default=None,
        description="조회 시작 날짜",
    ),
    end_date: datetime | None = Query(
        default=None,
        description="조회 종료 날짜",
    ),
    keyword: str | None = Query(
        default=None,
        description="도서명 검색 키워드",
    ),
    grade: ConditionGrade | None = Query(
        default=None,
        description="확정 검수 등급 필터(MINT, EXCELLENT, NORMAL, REJECT)",
    ),
    fast_track: bool | None = Query(
        default=None,
        description=(
            "Fast Track 여부 필터. true면 Fast Track 건만, "
            "false면 일반 검수 건만 조회"
        ),
    ),
    reason_code: str | None = Query(
        default=None,
        min_length=1,
        description="AI 판정 사유 코드 필터",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="페이지 번호",
    ),
    size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="페이지당 조회 건수",
    ),
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> InspectionHistoryListResponse:
    """
    관리자 검수 이력 그리드를 서버 페이지네이션 방식으로 조회한다.
    """
    return get_inspection_history(
        session=session,
        tenant_id=current_admin.tenant_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        grade=grade,
        fast_track=fast_track,
        reason_code=reason_code,
        page=page,
        size=size,
    )

@router.get(
    "/inspections/hitl-queue",
    response_model=HITLQueueListResponse,
    operation_id="getHitlInspectionQueue",
    summary="HITL 처리 보드 목록 조회",
)
def get_hitl_inspection_queue(
    bucket: HITLQueueBucket = Query(
        default=HITLQueueBucket.PENDING,
        description=(
            "보드 구분값: "
            "PENDING, IN_REVIEW, RECHECK, COMPLETED"
        ),
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="해당 보드의 페이지 번호",
    ),
    size: int = Query(
        default=10,
        ge=1,
        le=50,
        description="해당 보드에 한 번에 표시할 카드 수",
    ),
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> HITLQueueListResponse:
    return get_hitl_queue(
        session=session,
        tenant_id=current_admin.tenant_id,
        bucket=bucket,
        page=page,
        size=size,
    )

@router.get(
    "/inspections/hitl-queue/metrics",
    response_model=HITLQueueMetricsResponse,
    operation_id="getHitlInspectionQueueMetrics",
    summary="HITL 처리 보드 KPI 조회",
)
def get_hitl_inspection_queue_metrics(
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> HITLQueueMetricsResponse:
    return get_hitl_queue_metrics(
        session=session,
        tenant_id=current_admin.tenant_id,
    )

@router.get(
    "/inspections/{job_id}/agent-logs",
    response_model=list[AgentLogStep],
    summary="검수 Agent 단계별 실행 로그 조회",
    description=(
        "검수 작업에 저장된 Vision, Policy, Critic, Report Agent의 "
        "단계별 실행 로그를 반환합니다. 관리자 또는 MASTER 권한이 필요합니다."
    ),
    responses={
        401: {"description": "인증 토큰이 없거나 유효하지 않음"},
        403: {"description": "관리자 또는 MASTER 권한이 없음"},
        404: {"description": "검수 작업을 찾을 수 없음"},
    },
)
def get_admin_inspection_agent_logs(
    job_id: UUID,
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> list[AgentLogStep]:
    return get_inspection_agent_logs(
        session=session,
        tenant_id=current_admin.tenant_id,
        job_id=job_id,
    )

# 관리자용 개별 검수 상세 조회 API
@router.get(
    "/inspections/{job_id}",
    response_model=InspectionDetailResponse,
)
def get_admin_inspection_detail(
    job_id: UUID,
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> InspectionDetailResponse:
    return get_inspection_detail(
        session=session,
        tenant_id=current_admin.tenant_id,
        job_id=job_id,
    )


# FDS 기본 설정 시딩 헬퍼 함수
DEFAULT_POLICIES = [
    {
        "policy_key": "MAX_RETURN_30D",
        "policy_value": 3.0,
        "description": "최근 30일 내 최대 허용 반품 횟수 (초과 시 고의 파손 의심 분석 대상)"
    },
    {
        "policy_key": "MIN_UBCI_SCORE",
        "policy_value": 30.0,
        "description": "도서 등급 최하 한계 UBCI 점수 (이하인 경우 결함 의심)"
    },
    {
        "policy_key": "MAX_RETURN_90D",
        "policy_value": 5.0,
        "description": "최근 90일 내 최대 허용 반품 횟수 (초과 시 정밀 모니터링 경보)"
    },
    {
        "policy_key": "MAX_REFUND_AMT",
        "policy_value": 500000.0,
        "description": "누적 최대 허용 환불 금액 (원화 기준, 초과 시 위험군 분류)"
    }
]

def ensure_default_fds_policies(session: Session):
    existing = session.exec(select(FdsPolicy)).all()
    if not existing:
        for p in DEFAULT_POLICIES:
            policy = FdsPolicy(
                policy_key=p["policy_key"],
                policy_value=p["policy_value"],
                description=p["description"]
            )
            session.add(policy)
        session.commit()

# 1. GET /weekly-insights
@router.get(
    "/weekly-insights",
    response_model=list[WeeklyInsightResponse],
    summary="주간 누적 절감액 및 불량 분석 핫스팟 조회",
)
def get_weekly_insights(
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> list[WeeklyInsightResponse]:
    insights = session.exec(
        select(WeeklyInsight)
        .order_by(WeeklyInsight.report_week.desc())
    ).all()
    return insights

# 2. GET /fds/reports
@router.get(
    "/fds/reports",
    response_model=list[FdsReportResponse],
    summary="이상거래 위험군 탐지 기록 조회",
)
def get_fds_reports(
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> list[FdsReportResponse]:
    reports = session.exec(
        select(FdsReport)
        .where(FdsReport.tenant_id == current_admin.tenant_id)
        .order_by(FdsReport.detected_at.desc())
    ).all()

    # customer_id 별 customer_name 매핑
    customer_ids = [r.customer_id for r in reports if r.customer_id]
    customer_name_map = {}
    if customer_ids:
        # orders 테이블에서 고유한 B2B 고객사명 가져오기
        orders_data = session.exec(
            select(Order.customer_id, Order.customer_name)
            .where(Order.customer_id.in_(customer_ids))
        ).all()
        for cid, cname in orders_data:
            if cid and cname:
                customer_name_map[cid] = cname

    response_list = []
    for r in reports:
        response_list.append(
            FdsReportResponse(
                id=r.id,
                tenant_id=r.tenant_id,
                customer_id=r.customer_id,
                customer_name=customer_name_map.get(r.customer_id, "Unknown"),
                fraud_score=r.fraud_score,
                fraud_reason=r.fraud_reason,
                detected_at=r.detected_at,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
        )
    return response_list

# 3. GET /fds/policies
@router.get(
    "/fds/policies",
    response_model=list[FdsPolicyResponse],
    summary="FDS 룰셋 임계값 조회",
)
def get_fds_policies(
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> list[FdsPolicyResponse]:
    ensure_default_fds_policies(session)
    policies = session.exec(select(FdsPolicy)).all()
    return policies

# 4. PUT /fds/policies/{policy_key}
@router.put(
    "/fds/policies/{policy_key}",
    response_model=FdsPolicyResponse,
    summary="FDS 룰 임계값 단일 조절",
)
def update_fds_policy(
    policy_key: str,
    request: FdsPolicyUpdateRequest,
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> FdsPolicyResponse:
    ensure_default_fds_policies(session)
    policy = session.get(FdsPolicy, policy_key)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="FDS 정책을 찾을 수 없습니다.",
        )
    
    policy.policy_value = request.policy_value
    policy.updated_at = datetime.utcnow()
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy