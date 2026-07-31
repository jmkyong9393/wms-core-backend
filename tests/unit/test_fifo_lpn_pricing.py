from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.wms import ConditionGrade, OrderItem
from app.services.fifo_lpn_service import select_fifo_lpn_candidate


class CapturingResult:
    def first(self):
        return None


class CapturingSession:
    def __init__(self):
        self.statement = None

    def exec(self, statement):
        self.statement = statement
        return CapturingResult()


def test_fifo_lpn_query_only_considers_priced_inventory():
    session = CapturingSession()
    order_item = OrderItem(
        order_id=uuid4(),
        book_id=uuid4(),
        condition_grade=ConditionGrade.EXCELLENT,
        quantity=1,
        unit_price=Decimal("18000.00"),
        final_price=Decimal("18000.00"),
    )

    with pytest.raises(HTTPException):
        select_fifo_lpn_candidate(
            session=session,
            order_item=order_item,
            excluded_inventory_ids=set(),
        )

    statement = str(session.statement)
    assert "inventory_used_items.discount_rate IS NOT NULL" in statement
    assert "inventory_used_items.sale_price IS NOT NULL" in statement
