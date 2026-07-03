from fastapi import APIRouter, status, Depends
from sqlmodel import Session
from app.core.database import get_session
from app.models.wms import ReturnJob, Order, Book
from app.worker import process_book_inspection
from celery.result import AsyncResult
from app.worker import app as celery_app
import uuid

router = APIRouter()

@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_return_image(session: Session = Depends(get_session)):
    # 1. 외래키(FK) 제약조건 우회를 위한 더미 Order/Book 생성 로직 (시연용)
    order = session.query(Order).first()
    book = session.query(Book).first()
    
    if not order:
        order = Order(customer_name="Test Customer")
        session.add(order)
    if not book:
        book = Book(title="Test Book", isbn="1234567890")
        session.add(book)
    session.commit()
    
    # 2. Celery 워커로 비동기 작업 즉시 위임 (대기 안 함)
    image_url = "s3://dummy-bucket/test_return_image.jpg"
    task = process_book_inspection.apply_async(args=[str(order.id), image_url])
    
    # 3. DB에 초기 상태 기록 (task_id 포함)
    new_job = ReturnJob(
        order_id=order.id,
        book_id=book.id,
        status="PENDING",
        task_id=task.id,
        image_url=image_url
    )
    
    session.add(new_job)
    session.commit()
    session.refresh(new_job)
    
    return {
        "message": "Image uploaded and Celery task dispatched successfully.",
        "job_id": str(new_job.id),
        "task_id": task.id
    }

@router.get("/{task_id}")
def get_return_status(task_id: str, session: Session = Depends(get_session)):
    try:
        # Celery Result Backend(Redis)에서 실시간 상태 조회
        task_result = AsyncResult(task_id, app=celery_app)
        
        # DB에서도 조회 (옵션)
        from sqlmodel import select
        statement = select(ReturnJob).where(ReturnJob.task_id == task_id)
        job = session.exec(statement).first()
        
        return {
            "task_id": task_id,
            "celery_status": task_result.state,
            "db_status": job.status if job else "UNKNOWN",
            "agent_logs": job.agent_logs if job else None,
            "result": task_result.result if task_result.state == 'SUCCESS' else None
        }
    except Exception as e:
         return {"error": str(e)}
