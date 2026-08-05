from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    String,
    case,
    cast,
    func,
    literal,
    or_,
    union_all,
)
from sqlmodel import Session, select

from app.api.dependencies.auth import require_admin_or_master
from app.core.database import get_session
from app.models.wms import (
    Book,
    ConditionGrade,
    Inventory,
    InventoryUsedItem,
    Location,
    UsedInventoryStatus,
    User,
)

router = APIRouter()
v1_router = APIRouter()


class InventoryBookResponse(BaseModel):
    title: str = Field(description="도서명")
    isbn: str | None = Field(
        default=None,
        description="도서 ISBN",
    )


class InventoryListItemResponse(BaseModel):
    """
    통합 재고 목록의 한 행.

    quantity는 물리 재고 수량이며,
    실제 출고 가능 수량은 available_quantity를 사용한다.
    """

    id: UUID = Field(
        description="묶음 재고 또는 중고 단품 재고 ID",
    )
    book: InventoryBookResponse = Field(
        description="재고 도서 정보",
    )
    stock_type: Literal["NEW_STOCK", "USED_ITEM"] = Field(
        description="신간 묶음 재고 또는 중고·반품 단품 재고 구분",
    )
    grade: ConditionGrade | None = Field(
        description="중고·반품 단품 등급. 신간 재고는 null",
    )
    zone: str = Field(
        description="재고가 보관된 로케이션",
    )
    quantity: int = Field(
        description="물리 재고 수량. 중고 단품 재고는 항상 1",
    )
    reserved_quantity: int = Field(
        ge=0,
        description="다른 주문의 피킹 지시서에 이미 예약된 수량",
    )
    available_quantity: int = Field(
        ge=0,
        description="현재 주문·출고에 사용할 수 있는 가용 수량",
    )
    lpn_status: UsedInventoryStatus | None = Field(
        default=None,
        description=(
            "중고 LPN 단품 상태. 신간 묶음 재고는 null이며, "
            "중고 단품은 AVAILABLE 또는 RESERVED 상태를 반환한다."
        ),
    )
    lpn_barcode: str | None = Field(
        default=None,
        description=(
            "중고·반품 단품 식별용 LPN 바코드. "
            "신간 묶음 재고는 null"
        ),
    )
    base_price: Decimal = Field(
        description="도서 마스터에 저장된 정가",
    )
    discount_rate: Decimal | None = Field(
        default=None,
        description="현재 재고에 적용된 할인율",
    )
    sale_price: Decimal | None = Field(
        default=None,
        description="현재 재고의 판매가",
    )
    pricing_status: Literal[
        "DEFAULT_POLICY",
        "AGENT_PRICED",
        "PENDING",
    ] = Field(
        description="신간 기본 정책 또는 중고 Agent 가격 산정 상태",
    )
    date: datetime = Field(
        description="재고가 마지막으로 변경된 시각",
    )


class InventoryListResponse(BaseModel):
    """
    통합 재고 그리드의 서버 페이지네이션 응답.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "id": (
                            "00000000-0000-4000-8000-000000000001"
                        ),
                        "book": {
                            "title": "사피엔스",
                            "isbn": "9788912345678",
                        },
                        "stock_type": "NEW_STOCK",
                        "grade": "MINT",
                        "zone": "A-3-1",
                        "quantity": 10,
                        "reserved_quantity": 2,
                        "available_quantity": 8,
                        "lpn_status": None,
                        "lpn_barcode": None,
                        "base_price": "18000.00",
                        "discount_rate": "0.1000",
                        "sale_price": "16200.00",
                        "pricing_status": "DEFAULT_POLICY",
                        "date": "2026-08-03T10:00:00",
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "total_pages": 1,
            }
        }
    )

    items: list[InventoryListItemResponse] = Field(
        default_factory=list,
        description="현재 페이지의 통합 재고 목록",
    )
    total: int = Field(
        ge=0,
        description="전체 통합 재고 행 수",
    )
    page: int = Field(
        ge=1,
        description="현재 페이지 번호",
    )
    size: int = Field(
        ge=1,
        description="페이지당 조회 건수",
    )
    total_pages: int = Field(
        ge=0,
        description="전체 페이지 수",
    )


@router.get("/status")
def get_inventory_status():
    return {
        "total_books": 1500,
        "total_locations": 200,
        "recent_transactions": [
            {"type": "INBOUND", "quantity": 10},
            {"type": "OUTBOUND", "quantity": 2},
        ],
    }


def _format_location(
    barcode: str | None,
    zone: str | None,
    rack: str | None,
    shelf: str | None,
) -> str:
    """
    로케이션 바코드가 있으면 우선 반환하고,
    없으면 Zone-Rack-Shelf 조합을 반환한다.
    """
    if barcode:
        return barcode

    return f"{zone}-{rack}-{shelf}"


@v1_router.get(
    "",
    response_model=InventoryListResponse,
    operation_id="listInventory",
    summary="단품 및 묶음 재고 통합 조회",
    description=(
        "신간 묶음 재고와 중고·반품 LPN 단품 재고를 통합 조회합니다. "
        "ISBN을 전달하면 해당 도서의 로케이션별 재고만 조회합니다. "
        "관리자 또는 MASTER 권한이 필요합니다."
    ),
)
def list_inventory(
    isbn: str | None = Query(
        default=None,
        min_length=10,
        max_length=13,
        description="특정 ISBN의 로케이션별 재고만 조회",
    ),
    keyword: str | None = Query(
        default=None,
        description="도서명 또는 ISBN 부분 검색",
    ),
    grade: ConditionGrade | None = Query(
        default=None,
        description=(
            "재고 등급 필터. 신간 묶음 재고는 MINT로 조회된다."
        ),
    ),
    zone: str | None = Query(
        default=None,
        min_length=1,
        max_length=20,
        description="보관 구역 필터 예: A, B, C",
    ),
    start_date: date | None = Query(
        default=None,
        description="마지막 변경일 기준 조회 시작일(YYYY-MM-DD)",
    ),
    end_date: date | None = Query(
        default=None,
        description="마지막 변경일 기준 조회 종료일(YYYY-MM-DD)",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="페이지 번호",
    ),
    size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="페이지당 조회 건수",
    ),
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> InventoryListResponse:
    """
    신간과 중고 단품 재고를 통합해 서버 페이지네이션으로 조회한다.

    두 재고 소스를 UNION ALL로 합친 결과 전체를 기준으로
    COUNT, ORDER BY, OFFSET, LIMIT을 적용한다.
    """
    new_stock_statement = (
        select(
            Inventory.id.label("id"),
            Book.title.label("book_title"),
            Book.isbn.label("book_isbn"),
            literal("NEW_STOCK").label("stock_type"),
            literal(
                ConditionGrade.MINT.value,
            ).label("grade"),
            Location.barcode.label("barcode"),
            Location.zone.label("location_zone"),
            Location.rack.label("rack"),
            Location.shelf.label("shelf"),
            Inventory.quantity.label("quantity"),
            Inventory.reserved_quantity.label("reserved_quantity"),
            (
                Inventory.quantity - Inventory.reserved_quantity
            ).label("available_quantity"),
            cast(
                literal(None),
                String,
            ).label("lpn_status"),
            cast(
                literal(None),
                String,
            ).label("lpn_barcode"),
            Book.base_price.label("base_price"),
            Inventory.discount_rate.label("discount_rate"),
            Inventory.sale_price.label("sale_price"),
            case(
                (
                    Inventory.sale_price.is_not(None),
                    "DEFAULT_POLICY",
                ),
                else_="PENDING",
            ).label("pricing_status"),
            Inventory.updated_at.label("date"),
        )
        .join(
            Book,
            Inventory.book_id == Book.id,
        )
        .join(
            Location,
            Inventory.location_id == Location.id,
        )
        .where(
            Inventory.quantity > 0,
        )
    )

    used_item_statement = (
        select(
            InventoryUsedItem.id.label("id"),
            Book.title.label("book_title"),
            Book.isbn.label("book_isbn"),
            literal("USED_ITEM").label("stock_type"),
            cast(
                InventoryUsedItem.condition_grade,
                String,
            ).label("grade"),
            Location.barcode.label("barcode"),
            Location.zone.label("location_zone"),
            Location.rack.label("rack"),
            Location.shelf.label("shelf"),
            literal(1).label("quantity"),
            case(
                (
                    InventoryUsedItem.status
                    == UsedInventoryStatus.RESERVED,
                    1,
                ),
                else_=0,
            ).label("reserved_quantity"),
            case(
                (
                    InventoryUsedItem.status
                    == UsedInventoryStatus.AVAILABLE,
                    1,
                ),
                else_=0,
            ).label("available_quantity"),
            cast(
                InventoryUsedItem.status,
                String,
            ).label("lpn_status"),
            InventoryUsedItem.lpn_barcode.label("lpn_barcode"),
            Book.base_price.label("base_price"),
            InventoryUsedItem.discount_rate.label("discount_rate"),
            InventoryUsedItem.sale_price.label("sale_price"),
            case(
                (
                    InventoryUsedItem.sale_price.is_not(None),
                    "AGENT_PRICED",
                ),
                else_="PENDING",
            ).label("pricing_status"),
            InventoryUsedItem.updated_at.label("date"),
        )
        .join(
            Book,
            InventoryUsedItem.book_id == Book.id,
        )
        .join(
            Location,
            InventoryUsedItem.location_id == Location.id,
        )
        .where(
            InventoryUsedItem.status != UsedInventoryStatus.SHIPPED,
        )
    )

    if isbn is not None:
        new_stock_statement = new_stock_statement.where(
            Book.isbn == isbn,
        )
        used_item_statement = used_item_statement.where(
            Book.isbn == isbn,
        )

    normalized_keyword = (keyword or "").strip()

    if normalized_keyword:
        keyword_condition = or_(
            Book.title.ilike(
                f"%{normalized_keyword}%",
            ),
            Book.isbn.ilike(
                f"%{normalized_keyword}%",
            ),
        )
        new_stock_statement = new_stock_statement.where(
            keyword_condition,
        )
        used_item_statement = used_item_statement.where(
            keyword_condition,
        )

    if grade is not None:
        used_item_statement = used_item_statement.where(
            InventoryUsedItem.condition_grade == grade,
        )

        # 신간 묶음 재고는 항상 MINT로 취급한다.
        if grade != ConditionGrade.MINT:
            new_stock_statement = new_stock_statement.where(
                literal(False),
            )

    normalized_zone = (zone or "").strip().upper()

    if normalized_zone:
        new_stock_statement = new_stock_statement.where(
            Location.zone == normalized_zone,
        )
        used_item_statement = used_item_statement.where(
            Location.zone == normalized_zone,
        )

    if start_date is not None:
        new_stock_statement = new_stock_statement.where(
            func.date(Inventory.updated_at) >= start_date,
        )
        used_item_statement = used_item_statement.where(
            func.date(InventoryUsedItem.updated_at) >= start_date,
        )

    if end_date is not None:
        new_stock_statement = new_stock_statement.where(
            func.date(Inventory.updated_at) <= end_date,
        )
        used_item_statement = used_item_statement.where(
            func.date(InventoryUsedItem.updated_at) <= end_date,
        )

    inventory_union = union_all(
        new_stock_statement,
        used_item_statement,
    ).subquery("inventory_union")

    total = session.exec(
        select(func.count())
        .select_from(inventory_union)
    ).one()

    offset = (page - 1) * size

    rows = session.execute(
        select(inventory_union)
        .order_by(
            inventory_union.c.date.desc(),
            inventory_union.c.id.desc(),
        )
        .offset(offset)
        .limit(size)
    ).mappings().all()

    items: list[InventoryListItemResponse] = []

    for row_data in rows:
        items.append(
            InventoryListItemResponse(
                id=row_data["id"],
                book=InventoryBookResponse(
                    title=row_data["book_title"],
                    isbn=row_data["book_isbn"],
                ),
                stock_type=row_data["stock_type"],
                grade=row_data["grade"],
                zone=_format_location(
                    barcode=row_data["barcode"],
                    zone=row_data["location_zone"],
                    rack=row_data["rack"],
                    shelf=row_data["shelf"],
                ),
                quantity=row_data["quantity"],
                reserved_quantity=row_data["reserved_quantity"],
                available_quantity=row_data["available_quantity"],
                lpn_status=row_data["lpn_status"],
                lpn_barcode=row_data["lpn_barcode"],
                base_price=row_data["base_price"],
                discount_rate=row_data["discount_rate"],
                sale_price=row_data["sale_price"],
                pricing_status=row_data["pricing_status"],
                date=row_data["date"],
            )
        )

    total_pages = (
        (total + size - 1) // size
        if total > 0
        else 0
    )

    return InventoryListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        total_pages=total_pages,
    )


@v1_router.get(
    "/{inventory_id}",
    response_model=InventoryListItemResponse,
    operation_id="getNewStockInventoryDetail",
    summary="신간 묶음 재고 단건 조회",
    description=(
        "재고 ID를 기준으로 신간 묶음 재고의 도서, 로케이션, "
        "가용 수량과 현재 판매 가격을 조회합니다."
    ),
    responses={404: {"description": "신간 묶음 재고를 찾을 수 없음"}},
)
def get_new_stock_inventory_detail(
    inventory_id: UUID,
    current_admin: User = Depends(require_admin_or_master),
    session: Session = Depends(get_session),
) -> InventoryListItemResponse:
    row = session.exec(
        select(Inventory, Book, Location)
        .join(Book, Inventory.book_id == Book.id)
        .join(Location, Inventory.location_id == Location.id)
        .where(Inventory.id == inventory_id)
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="New stock inventory not found",
        )

    inventory, book, location = row
    return InventoryListItemResponse(
        id=inventory.id,
        book=InventoryBookResponse(title=book.title, isbn=book.isbn),
        stock_type="NEW_STOCK",
        grade=ConditionGrade.MINT,
        zone=_format_location(
            barcode=location.barcode,
            zone=location.zone,
            rack=location.rack,
            shelf=location.shelf,
        ),
        quantity=inventory.quantity,
        reserved_quantity=inventory.reserved_quantity,
        available_quantity=(
            inventory.quantity - inventory.reserved_quantity
        ),
        lpn_status=None,
        lpn_barcode=None,
        base_price=book.base_price,
        discount_rate=inventory.discount_rate,
        sale_price=inventory.sale_price,
        pricing_status=(
            "DEFAULT_POLICY"
            if inventory.sale_price is not None
            else "PENDING"
        ),
        date=inventory.updated_at,
    )
