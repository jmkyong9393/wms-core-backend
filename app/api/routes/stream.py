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

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# PENDING -> 20%, PROCESSING -> 50%, APPROVED, REJECTED -> 100%
def get_progress_by_status(status: str) -> int:
    if status == "PENDING":
        return 20
    
    if status == "PROCESSING":
        return 50
    
    if status in ["APPROVED","REJECTED","FAILED"]:
        return 100
    
    return 0

# 최종 상태 체크 함수
def is_terminal_status(status: str) -> bool:
    return status in ["APPROVED", "REJECTED", "FAILED"]

# SSE 메시지 포맷 함수 만들기
def format_sse_message(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"

# ReturnJob 상태를 주기적으로 조회해서 SSE 메시지로 전달하는 generator (fallback용)
async def generate_return_job_stream(return_job_id: UUID) -> AsyncGenerator[str, None]:
    while True:
        with Session(engine) as session:
            statement = select(ReturnJob).where(ReturnJob.id == return_job_id)
            job = session.exec(statement).first()

            if job is None:
                yield format_sse_message(
                    event = "error",
                    data = '{"message": "ReturnJob not found"}',
                )
                break

            progress = get_progress_by_status(job.status)

            data = json.dumps(
                {
                    "return_job_id": str(job.id),
                    "status": job.status,
                    "progress": progress,
                },
                ensure_ascii=False,
            )

            yield format_sse_message(
                event="progress",
                data=data,
            )

            if is_terminal_status(job.status):
                break
        await asyncio.sleep(1)

# Worker 상태 변경 시 Redis Pub/Sub로 진행 이벤트 발행
async def generate_return_job_pubsub_stream(
        return_job_id: UUID,
) -> AsyncGenerator[str, None]:
    redis_client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    pubsub = redis_client.pubsub()

    channel = f"return_job:{return_job_id}"

    await pubsub.subscribe(channel)

    try: 
        # 1. SSE 연결 직후 현재 DB 상태 1회 전송.
        with Session(engine) as session:
            statement = select(ReturnJob).where(ReturnJob.id == return_job_id)
            job = session.exec(statement).first()

            if job is None:
                yield format_sse_message(
                    event="error",
                    data = json.dumps(
                        {"message": "ReturnJob not found"},
                        ensure_ascii=False,
                    ),
                )
                return
            
            current_data = {
                "return_job_id": str(job.id),
                "status": job.status,
                "progress": get_progress_by_status(job.status),
                "ubci_score": job.ubci_score,
            }

            yield format_sse_message(
                event="progress",
                data=json.dumps(current_data, ensure_ascii=False),
            )

            if is_terminal_status(job.status):
                return
            
        # 2. 이후 상태 변경 이벤트는 Redis Pub/Sub로 수신한다.
        async for message in pubsub.listen():
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

            if is_terminal_status(parsed_data.get("status", "")):
                break

    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await redis_client.close()
            

# 프론트에 AI 검수 진행 상태를 SSE로 전달하는 API (fallback용)
@router.get("/returns/{return_job_id}")
async def stream_return_job_status(return_job_id: UUID):
    
    return StreamingResponse(
        generate_return_job_stream(return_job_id),
        media_type="text/event-stream",
    )

# 프론트에 AI 검수 진행 상태를 SSE로 전달하는 API
@router.get("/pubsub/returns/{return_job_id}")
async def stream_return_job_status_pubsub(return_job_id: UUID):
    return StreamingResponse(
        generate_return_job_pubsub_stream(return_job_id),
        media_type="text/event-stream",
    )
    