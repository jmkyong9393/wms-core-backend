from uuid import uuid4

from sqlalchemy import UniqueConstraint

from app.models.wms import PutawayJob, PutawayStatus


def test_putaway_status_lifecycle_values_are_fixed():
    assert [status.value for status in PutawayStatus] == [
        "WAITING",
        "COMPLETED",
        "CLEARED",
    ]


def test_putaway_job_starts_waiting_for_physical_storage():
    job = PutawayJob(
        inbound_item_id=uuid4(),
        location_id=uuid4(),
    )

    assert job.status == PutawayStatus.WAITING
    assert job.completed_at is None
    assert job.cleared_at is None


def test_inbound_item_has_only_one_putaway_job():
    inbound_item_constraint = next(
        constraint
        for constraint in PutawayJob.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
        and {
            column.name for column in constraint.columns
        } == {"inbound_item_id"}
    )

    assert inbound_item_constraint is not None
