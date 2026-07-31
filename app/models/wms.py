import uuid
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint
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


class BookCategory(str, Enum):
    COMIC = "COMIC"
    STUDY_GUIDE = "STUDY_GUIDE"
    NOVEL = "NOVEL"
    HUMANITIES = "HUMANITIES"
    SOCIAL_SCIENCE = "SOCIAL_SCIENCE"
    BUSINESS_ECONOMICS = "BUSINESS_ECONOMICS"
    SCIENCE_TECHNOLOGY = "SCIENCE_TECHNOLOGY"
    CHILDREN = "CHILDREN"
    LANGUAGE = "LANGUAGE"
    ART_LIFESTYLE = "ART_LIFESTYLE"


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
    RECHECK_REQUIRED = "RECHECK_REQUIRED"
    FAILED = "FAILED"

class OrderProposalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NOT_REQUIRED = "NOT_REQUIRED"

class InventoryTransactionType(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"
    RETURN_RESTOCK = "RETURN_RESTOCK"
    DISCARD = "DISCARD"


class UsedInventoryStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    SHIPPED = "SHIPPED"


class RejectedItemStatus(str, Enum):
    REJECT_HOLD = "REJECT_HOLD"
    DISCARDED = "DISCARDED"


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


class NotificationCategory(str, Enum):
    FDS_ALERT = "FDS_ALERT"
    AGENT_ALERT = "AGENT_ALERT"
    RESTOCK_ALERT = "RESTOCK_ALERT"


class NotificationSeverity(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


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
    category: BookCategory = Field(nullable=False)
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
    location_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="locations.id",
    )
    quantity: int = Field(nullable=False)
    lpn_barcode: Optional[str] = Field(default=None, unique=True)
    certificate_token: Optional[str] = Field(default=None, unique=True)
    condition_grade: Optional[ConditionGrade] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Location(SQLModel, table=True):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint(
            "zone IN ('A', 'B', 'C')",
            name="ck_locations_zone",
        ),
        CheckConstraint(
            "rack ~ '^[1-9][0-9]*$'",
            name="ck_locations_rack_positive_integer",
        ),
        CheckConstraint(
            "shelf ~ '^([1-9]|10)$'",
            name="ck_locations_shelf_range",
        ),
        CheckConstraint(
            "barcode = zone || '-' || rack || '-' || shelf",
            name="ck_locations_barcode_components",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    zone: str = Field(nullable=False)
    rack: str = Field(nullable=False)
    shelf: str = Field(nullable=False)
    barcode: str = Field(unique=True, nullable=False)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Inventory(SQLModel, table=True):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint("book_id", "location_id", name="uq_inventory_book_location"),
        CheckConstraint("reserved_quantity >= 0", name="ck_inventory_reserved_quantity_non_negative",),
        CheckConstraint("reserved_quantity <= quantity", name="ck_inventory_reserved_quantity_not_exceed_quantity",),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    quantity: int = Field(default=0)
    # 피킹 지시서에 배정되어 다른 주문이 사용할 수 없는 신간 재고 수량
    reserved_quantity: int = Field(default=0, nullable=False,)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InventoryUsedItem(SQLModel, table=True):
    __tablename__ = "inventory_used_items"
    __table_args__ = (
        CheckConstraint(
            "condition_grade IN ('MINT', 'EXCELLENT', 'NORMAL')",
            name="ck_inventory_used_items_sellable_grade",
        ),
        CheckConstraint(
            "discount_rate IS NULL OR "
            "(discount_rate >= 0 AND discount_rate < 1)",
            name="ck_inventory_used_items_discount_rate",
        ),
        CheckConstraint(
            "sale_price IS NULL OR sale_price > 0",
            name="ck_inventory_used_items_sale_price_positive",
        ),
        CheckConstraint(
            "(discount_rate IS NULL) = (sale_price IS NULL)",
            name="ck_inventory_used_items_pricing_pair",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: uuid.UUID = Field(foreign_key="locations.id")
    return_job_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="return_jobs.id",
        unique=True,
    )
    lpn_barcode: str = Field(nullable=False, unique=True)
    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    discount_rate: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 4)),
    )
    sale_price: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(12, 2)),
    )
    condition_grade: ConditionGrade = Field(nullable=False)
    status: UsedInventoryStatus = Field(
        default=UsedInventoryStatus.AVAILABLE,
        nullable=False,
    )
    certificate_url: Optional[str] = Field(default=None)
    stocked_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
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

    # 출고 확정 시 자동 발급되는 택배 송장 번호
    waybill_number: Optional[str] = Field(default=None,max_length=100,unique=True,index=True,)

    # 현재는 내부 발급용 기본 택배사 코드,
    # 실제 택배사 API 연동 시 해당 응답값을 저장
    shipping_carrier: Optional[str] = Field(default=None,max_length=50,)

    # 주문이 실제 출고 확정된 시각
    shipped_at: Optional[datetime] = Field(default=None,)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItem(SQLModel, table=True):
    __tablename__ = "order_items"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_id: uuid.UUID = Field(foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    location_id: Optional[uuid.UUID] = Field(default=None, foreign_key="locations.id")
    condition_grade: Optional[ConditionGrade] = Field(default=None)
    quantity: int = Field(nullable=False)
    unit_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    final_price: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class OrderItemInventoryAllocation(SQLModel, table=True):
    __tablename__ = "order_item_inventory_allocations"

    __table_args__ = (
        UniqueConstraint("order_item_id","inventory_id",name="uq_order_item_inventory_allocation",),
        CheckConstraint("quantity > 0",name="ck_order_item_inventory_allocation_quantity_positive",),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4,primary_key=True,)

    # 피킹 대상 주문 품목
    order_item_id: uuid.UUID = Field(foreign_key="order_items.id",nullable=False,index=True,)

    # 실제 예약한 신간 묶음 재고 행
    inventory_id: uuid.UUID = Field(foreign_key="inventory.id",nullable=False,index=True,)

    # 해당 재고 행에서 예약한 수량
    quantity: int = Field(nullable=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItemLpnAllocation(SQLModel, table=True):
    __tablename__ = "order_item_lpn_allocations"
    __table_args__ = (
        UniqueConstraint(
            "order_item_id",
            "inventory_used_item_id",
            name="uq_order_item_lpn_allocation",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    order_item_id: uuid.UUID = Field(
        foreign_key="order_items.id",
        nullable=False,
        index=True,
    )
    inventory_used_item_id: uuid.UUID = Field(
        foreign_key="inventory_used_items.id",
        nullable=False,
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OrderProposal(SQLModel, table=True):
    __tablename__ = "order_proposals"
    __table_args__ = (
        UniqueConstraint("return_job_id", name="uq_order_proposals_return_job",),
        CheckConstraint("pending_auto_po_quantity >= 0", name="ck_order_proposals_pending_auto_po_nonnegative",),)

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True,)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id",nullable=False,index=True,)
    book_id: uuid.UUID = Field(foreign_key="books.id",nullable=False,index=True,)
    return_job_id: uuid.UUID = Field(foreign_key="return_jobs.id",nullable=False,index=True,)
    recent_sales_quantity: int = Field(nullable=False)
    current_stock: int = Field(nullable=False)

    pending_auto_po_quantity: int = Field(default=0,nullable=False,)
    rejected_quantity: int = Field(nullable=False)
    rejection_reason_code: str = Field(nullable=False)

    recommended_order_quantity: int = Field(nullable=False)
    reason_summary: str = Field(nullable=False)
    evidence: list = Field(default_factory=list,sa_column=Column(JSONB, nullable=False),)
    risk_level: str = Field(nullable=False)

    status: OrderProposalStatus = Field(default=OrderProposalStatus.PENDING,nullable=False,index=True,)

    auto_po_order_id: uuid.UUID | None = Field(default=None,foreign_key="orders.id",index=True,)
    reviewer_id: uuid.UUID | None = Field(default=None,foreign_key="users.id",index=True,)
    reviewed_at: datetime | None = Field(default=None)
    review_comment: str | None = Field(default=None,max_length=1000,)
    created_at: datetime = Field(default_factory=datetime.utcnow,)
    updated_at: datetime = Field(default_factory=datetime.utcnow,)


class ReturnJob(SQLModel, table=True):
    __tablename__ = "return_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: UUID = Field(foreign_key="tenants.id", nullable=False, index=True,)

    order_id: Optional[uuid.UUID] = Field(default=None, foreign_key="orders.id")
    book_id: uuid.UUID = Field(foreign_key="books.id")
    inbound_item_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="inbound_items.id",
    )

    task_id: Optional[str] = Field(default=None)

    mode: InspectionMode = Field(nullable=False)

    status: ReturnJobStatus = Field(default=ReturnJobStatus.PENDING)

    image_paths: Optional[list] = Field(default=None, sa_column=Column(JSONB))

    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    condition_grade: Optional[ConditionGrade] = Field(default=None)

    agent_logs: Optional[dict] = Field(
        default_factory=dict,
        sa_column=Column(JSONB),
    )

    final_report: Optional[str] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RejectedItem(SQLModel, table=True):
    __tablename__ = "rejected_items"
    __table_args__ = (
        Index(
            "ix_rejected_items_status_location",
            "status",
            "location_id",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    inbound_item_id: uuid.UUID = Field(
        foreign_key="inbound_items.id",
        unique=True,
        nullable=False,
    )
    return_job_id: Optional[uuid.UUID] = Field(
        default=None,
        foreign_key="return_jobs.id",
        unique=True,
    )
    book_id: uuid.UUID = Field(foreign_key="books.id", nullable=False)
    location_id: uuid.UUID = Field(foreign_key="locations.id", nullable=False)
    lpn_barcode: str = Field(nullable=False, unique=True)
    ubci_score: Optional[Decimal] = Field(
        default=None,
        sa_column=Column(Numeric(5, 2)),
    )
    rejection_reason: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    status: RejectedItemStatus = Field(
        default=RejectedItemStatus.REJECT_HOLD,
        nullable=False,
    )
    rejected_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    discarded_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class InventoryLog(SQLModel, table=True):
    __tablename__ = "inventory_logs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    transaction_type: InventoryTransactionType = Field(nullable=False)
    book_id: uuid.UUID = Field(foreign_key="books.id")
    condition_grade: Optional[ConditionGrade] = Field(default=None)
    quantity_change: int = Field(nullable=False)
    target_lpn: Optional[str] = Field(default=None)
    picked_location: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class FdsReport(SQLModel, table=True):
    __tablename__ = "fds_reports"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    tenant_id: uuid.UUID = Field(foreign_key="tenants.id",nullable=False,index=True,)
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


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True,)
    tenant_id: UUID = Field(foreign_key="tenants.id",nullable=False,index=True,)
    category: NotificationCategory = Field(nullable=False,)
    severity: NotificationSeverity = Field(nullable=False,)
    title: str = Field(max_length=200,nullable=False,)
    message: str = Field(nullable=False,)
    payload: dict = Field(default_factory=dict,sa_column=Column(JSONB, nullable=False),)
    created_at: datetime = Field(default_factory=datetime.utcnow,)
    updated_at: datetime = Field(default_factory=datetime.utcnow,)


class NotificationRecipient(SQLModel, table=True):
    __tablename__ = "notification_recipients"
    __table_args__ = (UniqueConstraint("notification_id","user_id",name="uq_notification_recipient",),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4,primary_key=True,)
    notification_id: UUID = Field(foreign_key="notifications.id",nullable=False,index=True,)
    user_id: UUID = Field(foreign_key="users.id",nullable=False,index=True,)
    read_at: Optional[datetime] = Field(default=None,index=True,)
    created_at: datetime = Field(default_factory=datetime.utcnow,)
    updated_at: datetime = Field(default_factory=datetime.utcnow,)


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
