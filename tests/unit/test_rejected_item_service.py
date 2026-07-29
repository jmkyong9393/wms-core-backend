from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql

from app.models.wms import Location, RejectedItem, RejectedItemStatus
from app.services.rejected_item_service import discard_all_rejected_items


def test_discard_all_rejected_items_returns_zero_for_empty_zone():
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    discarded_count, discarded_at = discard_all_rejected_items(session)

    assert discarded_count == 0
    assert discarded_at is not None

    statement = session.exec.call_args.args[0]
    compiled = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "JOIN locations" in compiled
    assert "locations.zone = 'C'" in compiled
    assert f"rejected_items.status = '{RejectedItemStatus.REJECT_HOLD.value}'" in compiled
    assert "FOR UPDATE OF rejected_items" in compiled
