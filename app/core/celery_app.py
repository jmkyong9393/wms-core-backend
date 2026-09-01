import os

from celery import Celery
from kombu import Queue

# 실행 환경에 따라 Redis 주소를 환경변수에서 가져온다.
# 로컬: redis://localhost:6379/0
# Docker: redis://redis:6379/0
redis_url = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0",
)


celery_app = Celery(
    "wms_worker",
    broker=redis_url,
    backend=redis_url,
)


celery_app.conf.update(
    # Celery 메시지와 결과를 JSON으로 직렬화
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Flower 및 로그에서 사용할 시간대
    timezone="Asia/Seoul",
    enable_utc=False,
    # 작업이 시작되면 STARTED 상태 기록
    task_track_started=True,
    # Flower가 작업 이벤트를 받을 수 있도록 설정
    worker_send_task_events=True,
    task_send_sent_event=True,
    # Worker가 한 번에 가져가는 작업 수 제한
    worker_prefetch_multiplier=1,
    # 작업 완료 후 ACK 처리
    # Worker 장애 시 처리 중이던 작업을 다시 Queue에 등록
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Redis가 늦게 시작되더라도 Worker가 연결을 재시도
    broker_connection_retry_on_startup=True,
    # 작업 실행 제한 시간
    task_soft_time_limit=300,
    task_time_limit=360,
    task_default_queue="celery",
    task_default_routing_key="celery",
    task_queues=(
        Queue("celery", routing_key="celery"),
        Queue("inspection", routing_key="inspection"),
        Queue("restock", routing_key="restock"),
    ),
    task_routes={
        "app.worker.process_inspection": {
            "queue": "inspection",
            "routing_key": "inspection",
        },
        "app.worker.process_wms_action": {
            "queue": "inspection",
            "routing_key": "inspection",
        },
        "app.worker.process_restock_proposal": {
            "queue": "restock",
            "routing_key": "restock",
        },
        "app.worker.process_safety_stock_restock_proposal": {
            "queue": "restock",
            "routing_key": "restock",
        },
    },
)
