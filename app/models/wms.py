import uuid
from typing import Optional
from sqlmodel import Field, SQLModel, Column
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime

class Book(SQLModel, table=True):
    __tablename__ = "books"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(nullable=False)
    isbn: str = Field(nullable=False)
    virtual_stock: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Location(SQLModel, table=True):
    __tablename__ = "locations"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    zone: str = Field(nullable=False)
    rack: str = Field(nullable=False)
    shelf: str = Field(nullable=False)
    barcode: str = Field(nullable=False, unique=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Inventory(SQLModel, table=True):
    __tablename__ = "inventory"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    quantity: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class Order(SQLModel, table=True):
    __tablename__ = "orders"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_name: str = Field(nullable=False)
    status: str = Field(default="PENDING")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ReturnJob(SQLModel, table=True):
    __tablename__ = "return_jobs"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: Optional[str] = Field(default=None, index=True) # Celery의 task_id 매핑용
    order_id: uuid.UUID = Field(foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    status: str = Field(default="PENDING") # PENDING, PROCESSING, APPROVED, REJECTED
    image_url: Optional[str] = Field(default=None)
    agent_logs: Optional[dict] = Field(default={}, sa_column=Column(JSONB))
    final_report: Optional[str] = Field(default=None)
    ubci_score: Optional[int] = Field(default=None, description="자체 개발 상태 평가지수(UBCI) - 알라딘 참고 기반 중고 도서 상태 지수")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class InventoryLog(SQLModel, table=True):
    __tablename__ = "inventory_logs"
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    transaction_type: str = Field(nullable=False) # INBOUND, OUTBOUND, RETURN, DISCARD
    book_id: uuid.UUID = Field(foreign_key="books.id")
    quantity_change: int = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
