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
    RECEIVED = "RECEIVED"


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


class RestockProposalSource(str, Enum):
    RETURN_REJECTION = "RETURN_REJECTION"
    SAFETY_STOCK = "SAFETY_STOCK"


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
