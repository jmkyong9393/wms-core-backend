from fastapi import APIRouter, status, Depends
from sqlmodel import Session
from app.core.database import get_session
from app.models.wms import ReturnJob, Order, Book
import uuid

router = APIRouter()

@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
def upload_return_image(session: Session = Depends(get_session)):
    # Mitigation: 메모리 BackgroundTasks 대신 DB에 PENDING으로 Insert (PG Transaction Queue)
    
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
    
    # 2. PWA에서 전송된 이미지를 DB 큐에 PENDING으로 적재
    new_job = ReturnJob(
        order_id=order.id,
        book_id=book.id,
        status="PENDING",
        image_url="s3://dummy-bucket/test_return_image.jpg"
    )
    
    session.add(new_job)
    session.commit()
    session.refresh(new_job)
    
    return {
        "message": "Image uploaded and queued in DB successfully.",
        "job_id": str(new_job.id)
    }

@router.get("/{job_id}")
def get_return_status(job_id: str, session: Session = Depends(get_session)):
    try:
        job_uuid = uuid.UUID(job_id)
        job = session.get(ReturnJob, job_uuid)
        if not job:
            return {"error": "Job not found"}
        return {"job_id": str(job.id), "status": job.status, "agent_logs": job.agent_logs}
    except Exception as e:
         return {"error": "Invalid Job ID format"}
