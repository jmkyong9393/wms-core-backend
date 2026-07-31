from datetime import datetime
from uuid import UUID

from app.api.routes.inventory import list_inventory
from app.models.wms import UsedInventoryStatus


class FakeCountResult:
    def __init__(self, total):
        self.total = total

    def one(self):
        return self.total


class FakeMappingsResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeSession:
    def __init__(
        self,
        total,
        rows,
    ):
        self.total = total
        self.rows = rows
        self.count_statement = None
        self.list_statement = None

    def exec(self, statement):
        self.count_statement = statement
        return FakeCountResult(self.total)

    def execute(self, statement):
        self.list_statement = statement
        return FakeMappingsResult(self.rows)


def test_returns_new_and_used_inventory_availability_fields():
    session = FakeSession(
        total=2,
        rows=[
            {
                "id": UUID(
                    "00000000-0000-4000-8000-000000000001"
                ),
                "stock_type": "NEW_STOCK",
                "book_title": "신간 도서",
                "book_isbn": "9790000000001",
                "grade": None,
                "barcode": "A-1-1",
                "location_zone": "A",
                "rack": "1",
                "shelf": "1",
                "quantity": 10,
                "reserved_quantity": 3,
                "available_quantity": 7,
                "lpn_status": None,
                "date": datetime(2026, 7, 30, 10, 0, 0),
            },
            {
                "id": UUID(
                    "00000000-0000-4000-8000-000000000002"
                ),
                "stock_type": "USED_ITEM",
                "book_title": "중고 도서",
                "book_isbn": "9790000000002",
                "grade": "EXCELLENT",
                "barcode": "B-1-1",
                "location_zone": "B",
                "rack": "1",
                "shelf": "1",
                "quantity": 1,
                "reserved_quantity": 1,
                "available_quantity": 0,
                "lpn_status": "RESERVED",
                "date": datetime(2026, 7, 30, 9, 0, 0),
            },
        ],
    )

    response = list_inventory(
        isbn=None,
        page=1,
        size=20,
        current_admin=None,
        session=session,
    )

    new_stock = response.items[0]
    used_item = response.items[1]

    assert response.total == 2
    assert response.total_pages == 1

    assert new_stock.stock_type == "NEW_STOCK"
    assert new_stock.grade is None
    assert new_stock.quantity == 10
    assert new_stock.reserved_quantity == 3
    assert new_stock.available_quantity == 7
    assert new_stock.lpn_status is None

    assert used_item.stock_type == "USED_ITEM"
    assert used_item.quantity == 1
    assert used_item.reserved_quantity == 1
    assert used_item.available_quantity == 0
    assert used_item.lpn_status == UsedInventoryStatus.RESERVED


def test_applies_requested_offset_and_limit_to_inventory_query():
    session = FakeSession(
        total=50,
        rows=[],
    )

    response = list_inventory(
        isbn=None,
        page=3,
        size=10,
        current_admin=None,
        session=session,
    )

    assert response.total == 50
    assert response.page == 3
    assert response.size == 10
    assert response.total_pages == 5

    assert session.list_statement._offset_clause.value == 20
    assert session.list_statement._limit_clause.value == 10


def test_applies_isbn_filter_to_new_and_used_inventory_query():
    session = FakeSession(
        total=0,
        rows=[],
    )

    list_inventory(
        isbn="9790000000001",
        page=1,
        size=20,
        current_admin=None,
        session=session,
    )

    compiled_params = session.list_statement.compile().params

    assert sum(
        value == "9790000000001"
        for value in compiled_params.values()
    ) == 2


def test_excludes_shipped_lpn_from_inventory_query():
    session = FakeSession(
        total=0,
        rows=[],
    )

    list_inventory(
        isbn=None,
        page=1,
        size=20,
        current_admin=None,
        session=session,
    )

    statement_sql = str(session.list_statement)

    assert "inventory_used_items.status !=" in statement_sql


def test_returns_zero_total_pages_when_inventory_is_empty():
    session = FakeSession(
        total=0,
        rows=[],
    )

    response = list_inventory(
        isbn=None,
        page=1,
        size=20,
        current_admin=None,
        session=session,
    )

    assert response.items == []
    assert response.total == 0
    assert response.total_pages == 0