import asyncio
import json
import os
from collections.abc import AsyncGenerator
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.core.database import engine
from app.models.wms import ReturnJob, ReturnJobStatus
from app.services.redis_pubsub import get_return_job_channel


router = APIRouter()

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)

POLLING_INTERVAL_SECONDS = 1

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

# SSE 헤더 통일
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
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

# ReturnJob 조회 중복 분리
def find_return_job(
    job_id: UUID,
) -> ReturnJob | None:
    with Session(engine) as session:
        statement = select(ReturnJob).where(
            ReturnJob.id == job_id
        )
        return session.exec(statement).first()

# SSE 응답 데이터 생성 중복 분리
def build_job_event_data(
    job: ReturnJob,
) -> dict[str, object]:
    status = normalize_status(job.status)

    return {
        "job_id": str(job.id),
        "status": status,
        "progress": get_progress_by_status(status),
        "ubci_score": job.ubci_score,
    }

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

# DB 상태를 주기적으로 조회하는 fallback generator
async def generate_inspection_fallback_stream(
    job_id: UUID,
) -> AsyncGenerator[str, None]:

    while True:
        job = find_return_job(job_id)
        
        if job is None:
            yield build_job_not_found_message()
            return
        
        event_data = build_job_event_data(job)

        yield format_sse_message(
            event="progress",
            data=json.dumps(
                event_data,
                ensure_ascii=False,
            ),
        )

        if is_terminal_status(job.status):
            return

        await asyncio.sleep(POLLING_INTERVAL_SECONDS)


# Redis Pub/Sub 이벤트를 실시간 전달하는 기본 generator
async def generate_inspection_pubsub_stream(
    job_id: UUID,
) -> AsyncGenerator[str, None]:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )
    pubsub = redis_client.pubsub()
    channel = get_return_job_channel(
        str(job_id)
    )

    await pubsub.subscribe(channel)

    try:
        # 1. SSE 연결 직후 DB 현재 상태를 1회 전달
        # Pub/Sub 연결 전에 발생한 이벤트 유실을 보완한다.
        job = find_return_job(job_id)

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

        # 이미 완료된 작업이면 바로 연결 종료
        if is_terminal_status(job.status):
            return

        # 2. 이후 변경사항은 Redis Pub/Sub로 실시간 수신
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            data = message["data"]

            yield format_sse_message(
                event="progress",
                data=data,
            )

            # 최종 상태인지 확인하기 위해 JSON 파싱
            try:
                parsed_data = json.loads(data)
            except json.JSONDecodeError:
                continue

            if is_terminal_status(
                parsed_data.get("status", "")
            ):
                return

    finally:
        # SSE 연결 종료 시 Redis 자원 정리
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_client.close()


# 기본 SSE API: Redis Pub/Sub 방식
@router.get("/{job_id}/stream")
async def stream_inspection_status(
    job_id: UUID,
) -> StreamingResponse:
    return StreamingResponse(
        generate_inspection_pubsub_stream(job_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


# fallback SSE API: DB polling 방식
@router.get("/{job_id}/stream/fallback")
async def stream_inspection_status_fallback(
    job_id: UUID,
) -> StreamingResponse:
    return StreamingResponse(
        generate_inspection_fallback_stream(job_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )