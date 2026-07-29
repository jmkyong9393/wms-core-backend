from unittest.mock import MagicMock

from app.services.rejected_item_service import discard_all_rejected_items


def test_discard_all_rejected_items_returns_zero_for_empty_zone():
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    discarded_count, discarded_at = discard_all_rejected_items(session)

    assert discarded_count == 0
    assert discarded_at is not None
