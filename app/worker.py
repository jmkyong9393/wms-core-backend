import time
import os
from celery import Celery
from sqlmodel import Session
from app.core.database import engine
from app.models.wms import ReturnJob

# Celery Broker 연동 (Managed Redis 권장)
broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
app = Celery('wms_tasks', broker=broker_url, backend=broker_url)

app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Seoul',
    enable_utc=True,
    worker_concurrency=2, # OOM 방지를 위해 워커당 동시성 제한
    task_soft_time_limit=30, # AI API 무한 대기 방지
    task_time_limit=45
)

@app.task(bind=True, max_retries=3)
def process_book_inspection(self, order_id: str, image_url: str):
    """
    Celery 기반 비동기 백그라운드 워커.
    LangGraph Supervisor 파이프라인을 호출하여 상태를 분석하고, 결과를 Redis 및 DB에 기록합니다.
    """
    try:
        task_id = self.request.id
        print(f"[Celery Worker] Started Task {task_id} for Order {order_id}")
        
        # 1. DB 상태 업데이트 (PROCESSING)
        with Session(engine) as session:
            # task_id로 찾기
            from sqlmodel import select
            statement = select(ReturnJob).where(ReturnJob.task_id == task_id)
            job = session.exec(statement).first()
            if job:
                job.status = 'PROCESSING'
                session.commit()
                print(f"[Celery Worker] Job {job.id} marked as PROCESSING.")
        
        # -------------------------------------------------------------
        # TODO: 2. LangGraph Supervisor (Star Topology) 호출 로직 구현
        # -------------------------------------------------------------
        # app.ai.supervisor.app_graph 를 import 한 뒤 invoke() 하세요.
        print(f"[Celery Worker] Running LangGraph Supervisor Pipeline for task {task_id}... (Not Implemented)")
        time.sleep(2) # 더미 대기
        
        # -------------------------------------------------------------
        # TODO: 3. 결과 분석 및 DB 트랜잭션 종료 로직 구현
        # -------------------------------------------------------------
        # 그래프가 종료되면 반환된 ubci_score와 final_report를 추출하여 
        # status='APPROVED' 또는 'WAITING_HUMAN'과 함께 UPDATE 하세요.
        with Session(engine) as session:
            statement = select(ReturnJob).where(ReturnJob.task_id == task_id)
            job = session.exec(statement).first()
            if job:
                job.status = 'WAITING_HUMAN' # 예시
                job.ubci_score = 85
                session.commit()
                
        print(f"[Celery Worker] Task {task_id} completed.")
        return {"status": "SUCCESS", "ubci_score": 85}
        
    except Exception as exc:
        print(f"[Celery Worker Error] {exc}")
        # DB 상태를 FAILED로 변경하는 로직 추가 가능
        raise self.retry(exc=exc, countdown=5) # 5초 후 재시도
