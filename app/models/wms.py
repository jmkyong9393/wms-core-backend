import uuid
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Numeric
from sqlmodel import Field, SQLModel


class StandardSize(str, Enum):
    A5 = "A5"
    B5 = "B5"

class InboundType(str, Enum):
    NEW_STOCK = "NEW_STOCK"
    USED_PURCHASE = "USED_PURCHASE"
    CUSTOMER_RETURN = "CUSTOMER_RETURN"


class InboundStatus(str, Enum):
    RECEIVED = "RECEIVED"
    CHECKING = "CHECKING"
    COMPLETED = "COMPLETED"


class ConditionGrade(str, Enum):
    MINT = "MINT"
    EXCELLENT = "EXCELLENT"
    NORMAL = "NORMAL"
    REJECT = "REJECT"


class OrderType(str, Enum):
    B2B_ORDER = "B2B_ORDER"
    AUTO_PO = "AUTO_PO"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PICKING = "PICKING"
    SHIPPED = "SHIPPED"
    RETURN_REQUESTED = "RETURN_REQUESTED"


class ReturnJobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    HITL_REQUIRED = "HITL_REQUIRED"
    FAILED = "FAILED"


class InventoryTransactionType(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    RETURN_RESTOCK = "RETURN_RESTOCK"
    DISCARD = "DISCARD"


class UserRole(str, Enum):
    MASTER = "MASTER"
    ADMIN = "ADMIN"
    WORKER = "WORKER"
    GUEST = "GUEST"
    PENDING = "PENDING"


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class TicketStatus(str, Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"


class PostCategory(str, Enum):
    NOTICE = "NOTICE"
    MANUAL = "MANUAL"
    GENERAL = "GENERAL"

class InspectionMode(str, Enum):
    RETURN = "RETURN"
    USED_PURCHASE = "USED_PURCHASE"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    code: str = Field(max_length=50, unique=True, index=True, nullable=False)
    name: str = Field(max_length=100, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class FdsPolicy(SQLModel, table=True):
    __tablename__ = "fds_policies"

    policy_key: str = Field(max_length=100, primary_key=True)
    policy_value: Decimal = Field(nullable=False)
    description: Optional[str] = Field(default=None, max_length=500)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class Book(SQLModel, table=True):
    __tablename__ = "books"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    title: str = Field(nullable=False)
    isbn: Optional[str] = Field(default=None, unique=True)
    publisher: Optional[str] = Field(default=None)
    standard_size: Optional[StandardSize] = Field(default=None)
    thickness_mm: Optional[int] = Field(default=None)
    base_price: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 2), nullable=False, default=0),
    )
    virtual_stock: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InboundJob(SQLModel, table=True):
    __tablename__ = "inbound_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inbound_type: InboundType = Field(nullable=False)
    status: InboundStatus = Field(nullable=False)
    supplier_name: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InboundItem(SQLModel, table=True):
    __tablename__ = "inbound_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inbound_job_id: uuid.UUID = Field(foreign_key="inbound_jobs.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    quantity: int = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Location(SQLModel, table=True):
    __tablename__ = "locations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    zone: str = Field(nullable=False)
    rack: str = Field(nullable=False)
    shelf: str = Field(nullable=False)
    barcode: Optional[str] = Field(default=None, unique=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Inventory(SQLModel, table=True):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("book_id", "location_id", name="uq_inventory_book_location"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    quantity: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InventoryUsedItem(SQLModel, table=True):
    __tablename__ = "inventory_used_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    lpn_barcode: str = Field(nullable=False, unique=True)
    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    condition_grade: ConditionGrade = Field(nullable=False)
    certificate_url: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: Optional[uuid.UUID] = Field(default=None)
    customer_name: Optional[str] = Field(default=None)
    type: OrderType = Field(nullable=False)
    total_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    status: OrderStatus = Field(nullable=False)
    logistics_center: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_id: uuid.UUID = Field(foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: Optional[uuid.UUID] = Field(default=None, foreign_key="locations.id")
    quantity: int = Field(nullable=False)
    unit_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    final_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReturnJob(SQLModel, table=True):
    __tablename__ = "return_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", nullable=False, index=True,)

    order_id: Optional[uuid.UUID] = Field(default=None, foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")

    task_id: Optional[str] = Field(default=None)

    mode: InspectionMode = Field(nullable=False)

    status: ReturnJobStatus = Field(default=ReturnJobStatus.PENDING)

    image_paths: Optional[list] = Field(default=None, sa_column=Column(JSONB))

    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )

    agent_logs: Optional[dict] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

    final_report: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InventoryLog(SQLModel, table=True):
    __tablename__ = "inventory_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    transaction_type: InventoryTransactionType = Field(nullable=False)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    condition_grade: ConditionGrade = Field(nullable=False)
    quantity_change: int = Field(nullable=False)
    target_lpn: Optional[str] = Field(default=None)
    picked_location: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FdsReport(SQLModel, table=True):
    __tablename__ = "fds_reports"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    customer_id: uuid.UUID = Field(nullable=False)
    fraud_score: int = Field(nullable=False)
    fraud_reason: Optional[str] = Field(default=None)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WeeklyInsight(SQLModel, table=True):
    __tablename__ = "weekly_insights"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    report_week: str = Field(nullable=False, unique=True)
    saved_labor_cost_krw: int = Field(default=0)
    top_defective_publishers: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    location_hotspots: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    logistics_hotspots: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    predicted_returns: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", nullable=False, index=True, )
    employee_id: str = Field(    # 사번은 필수값, 중복 불가로 변경
        nullable=False,
        unique=True,
        index=True,
    )
    email: Optional[str] = Field(    # 이메일 중복 불가로 변경
        default=None,
        unique=True,
        index=True,
    )
    name: str = Field(nullable=False)
    password_hash: str = Field(nullable=False)
    role: UserRole = Field(nullable=False)
    status: UserStatus = Field(default=UserStatus.ACTIVE)

    # 관리자가 직원 계정 만들면 True -> 직원이 임시 비밀번호를 새 비밀번호로 변경하면 False
    must_change_password: bool = Field(default=True)

    last_login: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Board(SQLModel, table=True):
    __tablename__ = "boards"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="return_jobs.id")
    ticket_status: TicketStatus = Field(nullable=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BoardPost(SQLModel, table=True):
    __tablename__ = "board_posts"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    author_id: uuid.UUID = Field(foreign_key="users.id")
    category: PostCategory = Field(nullable=False)
    title: str = Field(nullable=False)
    content: str = Field(nullable=False)
    attachment_paths: Optional[list] = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
