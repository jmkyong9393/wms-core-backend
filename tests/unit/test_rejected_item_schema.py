from uuid import uuid4

from app.models.wms import InventoryUsedItem, RejectedItem, RejectedItemStatus


def test_rejected_item_is_not_a_sellable_inventory_record():
    rejected_item = RejectedItem(
        inbound_item_id=uuid4(),
        return_job_id=uuid4(),
        book_id=uuid4(),
        location_id=uuid4(),
        lpn_barcode="LPN-REJECTED-001",
    )

    assert rejected_item.status == RejectedItemStatus.REJECT_HOLD
    assert rejected_item.discarded_at is None


def test_rejected_item_has_one_record_per_inbound_item():
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in RejectedItem.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }

    assert ("inbound_item_id",) in unique_columns
    assert ("lpn_barcode",) in unique_columns


def test_used_inventory_accepts_only_sellable_used_grades():
    check_names = {
        constraint.name
        for constraint in InventoryUsedItem.__table__.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }

    assert "ck_inventory_used_items_sellable_grade" in check_names


def test_rejected_items_have_bulk_clear_lookup_index():
    index_names = {index.name for index in RejectedItem.__table__.indexes}

    assert "ix_rejected_items_status_location" in index_names
