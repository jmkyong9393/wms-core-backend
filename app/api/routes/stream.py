import asyncio
import json
import os
from typing import AsyncGenerator
from uuid import UUID

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.core.database import engine
from app.models.wms import ReturnJob


router = APIRouter()

REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)


# 상태값을 화면 표시용 진행률로 변환
def get_progress_by_status(status: str) -> int:
    if status == "PENDING":
        return 20

    if status == "PROCESSING":
        return 50

    # 관리자 확인이 필요한 상태
    if status == "HITL_REQUIRED":
        return 70

    # 최종 종료 상태
    if status in ["APPROVED", "REJECTED", "FAILED"]:
        return 100

    return 0


# SSE 연결을 종료할 최종 상태인지 확인
def is_terminal_status(status: str) -> bool:
    return status in [
        "APPROVED",
        "REJECTED",
        "FAILED",
    ]


# SSE 규격에 맞는 문자열 생성
def format_sse_message(
    event: str,
    data: str,
) -> str:
    return f"event: {event}\ndata: {data}\n\n"


# DB 상태를 주기적으로 조회하는 fallback generator
async def generate_inspection_fallback_stream(
    job_id: UUID,
) -> AsyncGenerator[str, None]:

    while True:
        with Session(engine) as session:
            statement = select(ReturnJob).where(
                ReturnJob.id == job_id
            )
            job = session.exec(statement).first()

            # 존재하지 않는 검수 작업
            if job is None:
                yield format_sse_message(
                    event="error",
                    data=json.dumps(
                        {
                            "message": (
                                "검수 작업을 찾을 수 없습니다."
                            )
                        },
                        ensure_ascii=False,
                    ),
                )
                break

            # 현재 DB 상태를 SSE 데이터로 생성
            data = json.dumps(
                {
                    "job_id": str(job.id),
                    "status": job.status,
                    "progress": get_progress_by_status(
                        job.status
                    ),
                    "ubci_score": job.ubci_score,
                },
                ensure_ascii=False,
            )

            yield format_sse_message(
                event="progress",
                data=data,
            )

            # 최종 상태에 도달하면 연결 종료
            if is_terminal_status(job.status):
                break

        # 1초마다 DB 상태 확인
        await asyncio.sleep(1)


# Redis Pub/Sub 이벤트를 실시간 전달하는 기본 generator
async def generate_inspection_pubsub_stream(
    job_id: UUID,
) -> AsyncGenerator[str, None]:

    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )
    pubsub = redis_client.pubsub()

    # Worker의 publish 채널명과 동일해야 함
    channel = f"return_job:{job_id}"

    await pubsub.subscribe(channel)

    try:
        # 1. SSE 연결 직후 DB 현재 상태를 1회 전달
        # Pub/Sub 연결 전에 발생한 이벤트 유실을 보완한다.
        with Session(engine) as session:
            statement = select(ReturnJob).where(
                ReturnJob.id == job_id
            )
            job = session.exec(statement).first()

            if job is None:
                yield format_sse_message(
                    event="error",
                    data=json.dumps(
                        {
                            "message": (
                                "검수 작업을 찾을 수 없습니다."
                            )
                        },
                        ensure_ascii=False,
                    ),
                )
                return

            current_data = {
                "job_id": str(job.id),
                "status": job.status,
                "progress": get_progress_by_status(
                    job.status
                ),
                "ubci_score": job.ubci_score,
            }

            yield format_sse_message(
                event="progress",
                data=json.dumps(
                    current_data,
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
                break

    finally:
        # SSE 연결 종료 시 Redis 자원 정리
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_client.close()


# 기본 SSE API: Redis Pub/Sub 방식
@router.get("/{job_id}/stream")
async def stream_inspection_status(
    job_id: UUID,
):
    return StreamingResponse(
        generate_inspection_pubsub_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# fallback SSE API: DB polling 방식
@router.get("/{job_id}/stream/fallback")
async def stream_inspection_status_fallback(
    job_id: UUID,
):
    return StreamingResponse(
        generate_inspection_fallback_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )