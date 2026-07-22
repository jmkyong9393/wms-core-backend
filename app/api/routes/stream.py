import json
from collections.abc import AsyncGenerator
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import engine
from app.models.wms import ReturnJob, ReturnJobStatus
from app.services.redis_pubsub import get_return_job_channel
from app.services.sse_ticket_service import validate_sse_ticket


router = APIRouter()


# 상태별 진행률
STATUS_PROGRESS = {
    ReturnJobStatus.PENDING.value: 20,
    ReturnJobStatus.PROCESSING.value: 50,
    ReturnJobStatus.HITL_REQUIRED.value: 70,
    ReturnJobStatus.APPROVED.value: 100,
    ReturnJobStatus.REJECTED.value: 100,
    ReturnJobStatus.FAILED.value: 100,
}

# 종료 상태
TERMINAL_STATUSES = {
    ReturnJobStatus.APPROVED.value,
    ReturnJobStatus.REJECTED.value,
    ReturnJobStatus.FAILED.value,
}

# SSE 응답의 캐싱과 프록시 버퍼링 방지
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Enum과 문자열 상태 통일
def normalize_status(status: ReturnJobStatus | str) -> str:
    if isinstance(status, ReturnJobStatus):
        return status.value
    
    return str(status)

# 상태값을 화면 표시용 진행률로 변환
def get_progress_by_status(
    status: ReturnJobStatus | str,
) -> int:
    return STATUS_PROGRESS.get(
        normalize_status(status),
        0,
    )


# SSE 연결을 종료할 최종 상태인지 확인
def is_terminal_status(
    status: ReturnJobStatus | str,
) -> bool:
    return normalize_status(status) in TERMINAL_STATUSES


# SSE 규격에 맞는 문자열 생성
def format_sse_message(
    event: str,
    data: str,
) -> str:
    return f"event: {event}\ndata: {data}\n\n"

# SSE 연결 유지를 위한 Heartbeat 생성
def format_sse_heartbeat() -> str:
    return ": heartbeat\n\n"

# 브라우저 EventSource의 자동 재접속 대기시간 설정
def format_sse_retry(
    retry_milliseconds: int,
) -> str:
    return f"retry: {retry_milliseconds}\n\n"

# ReturnJob 조회 중복 분리
def find_return_job(
    job_id: UUID,
    tenant_id: UUID,
) -> ReturnJob | None:
    with Session(engine) as session:
        statement = select(ReturnJob).where(
            ReturnJob.id == job_id,
            ReturnJob.tenant_id == tenant_id,
        )
        return session.exec(statement).first()


# SSE 재접속 시에도 실시간 Worker 이벤트와 동일한 기본 필드를 전달
def build_job_event_data(
    job: ReturnJob,
) -> dict[str, object]:
    status = normalize_status(job.status)
    agent_logs = job.agent_logs or {}

    event_data: dict[str, object] = {
        "job_id": str(job.id),
        "task_id": job.task_id,
        "status": status,
        "progress": get_progress_by_status(status),
        "ubci_score": job.ubci_score,
    }

    # WMS 후속 Task 등록에 실패한 작업은 SSE 재접속 시에도 실패 정보를 전달
    if agent_logs.get("wms_dispatch_failed") is True:
        event_data.update({
            "wms_dispatch_failed": True,
            "error_message": agent_logs.get(
                "wms_dispatch_error"
            ),
            "wms_dispatch_failed_at": agent_logs.get(
                "wms_dispatch_failed_at"
            ),
        })
    
    # 실패 이벤트를 놓친 뒤 재접속해도 실패 단계를 구분할 수 있도록 전달
    if status == ReturnJobStatus.FAILED.value:
        if isinstance(agent_logs.get("wms_error"), dict):
            wms_error = agent_logs["wms_error"]

            event_data.update({
                "failure_stage": "WMS_PROCESSING",
                "error_message": wms_error.get("message"),
            })

        elif isinstance(agent_logs.get("error"), dict):
            inspection_error = agent_logs["error"]

            event_data.update({
                "failure_stage": "AI_INSPECTION",
                "error_message": inspection_error.get("message"),
            })

    return event_data


# 오류 메시지 중복 분리
def build_job_not_found_message() -> str:
    return format_sse_message(
        event="error",
        data=json.dumps(
            {
                "message": "검수 작업을 찾을 수 없습니다.",
            },
            ensure_ascii=False,
        ),
    )



# Redis Pub/Sub 이벤트와 Heartbeat를 SSE로 전달
async def generate_inspection_pubsub_stream(
    job_id: UUID,
    tenant_id: UUID,
    request: Request,
) -> AsyncGenerator[str, None]:
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )
    pubsub = redis_client.pubsub()
    channel = get_return_job_channel(str(job_id))

    try:
        await pubsub.subscribe(channel)

        # 브라우저의 자동 재접속 대기시간 설정
        yield format_sse_retry(
            settings.SSE_RETRY_MILLISECONDS
        )

        # 1. SSE 연결 직후 DB 현재 상태를 1회 전달
        job = find_return_job(
            job_id=job_id,
            tenant_id=tenant_id,
        )

        if job is None:
            yield build_job_not_found_message()
            return

        yield format_sse_message(
            event="progress",
            data=json.dumps(
                build_job_event_data(job),
                ensure_ascii=False,
            ),
        )

        if is_terminal_status(job.status):
            return

        # 2. Redis 이벤트를 기다리며 일정 시간마다 Heartbeat 전달
        while True:
            # 브라우저가 SSE 연결을 종료했다면 Redis 구독도 정리
            if await request.is_disconnected():
                return

            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=settings.SSE_HEARTBEAT_SECONDS,
            )

            # 설정 시간 동안 Redis 이벤트가 없으면 연결 유지용 Heartbeat 전달
            if message is None:
                yield format_sse_heartbeat()
                continue

            if message["type"] != "message":
                continue

            data = message["data"]

            yield format_sse_message(
                event="progress",
                data=data,
            )

            try:
                parsed_data = json.loads(data)
            except json.JSONDecodeError:
                continue

            if is_terminal_status(
                parsed_data.get("status", "")
            ):
                return

    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass

        await pubsub.aclose()
        await redis_client.aclose()


# 기본 SSE API: Redis Pub/Sub 방식
@router.get(
    "/{job_id}/stream",
    response_class=StreamingResponse,
)
async def stream_inspection_status(
    request: Request,
    job_id: UUID,
    ticket: str = Query(
        ...,
        min_length=20,
    ),
) -> StreamingResponse:
    ticket_payload = await validate_sse_ticket(
        ticket=ticket,
        job_id=job_id,
    )

    if ticket_payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않거나 만료된 SSE 티켓입니다.",
        )

    try:
        ticket_tenant_id = UUID(
            str(ticket_payload["tenant_id"])
        )
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSE 티켓의 테넌트 정보가 올바르지 않습니다.",
        )

    return StreamingResponse(
        generate_inspection_pubsub_stream(
            job_id=job_id,
            tenant_id=ticket_tenant_id,
            request=request,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
