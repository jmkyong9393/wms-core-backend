from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, text
from sqlmodel import Session, select

from app.domain.warehouse_location_policy import (
    SHELF_CAPACITY,
    SHELF_COUNT_PER_RACK,
    build_location_barcode,
    rack_for_category,
    zone_for_grade,
)
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    Inventory,
    InventoryUsedItem,
    Location,
    PutawayJob,
    PutawayStatus,
    UsedInventoryStatus,
)


class NoAvailableLocationError(Exception):
    pass


@dataclass(frozen=True)
class LocationAssignment:
    putaway_job: PutawayJob
    location: Location


def assign_putaway_location(
    session: Session,
    inbound_item: InboundItem,
    book: Book,
    grade: ConditionGrade,
) -> LocationAssignment:
    zone = zone_for_grade(grade)
    rack = rack_for_category(book.category)
    _lock_zone_rack(session, zone, rack)

    existing_job = session.exec(
        select(PutawayJob)
        .where(PutawayJob.inbound_item_id == inbound_item.id)
        .with_for_update()
    ).first()

    shelf_locations: dict[str, Location] = {}
    shelf_occupancies: dict[str, int] = {}
    for shelf_number in range(1, SHELF_COUNT_PER_RACK + 1):
        shelf = str(shelf_number)
        location = _get_or_create_location(session, zone, rack, shelf)
        if not location.is_active:
            continue

        shelf_locations[shelf] = location
        shelf_occupancies[shelf] = _count_location_occupancy(
            session=session,
            location=location,
            current_putaway_job_id=existing_job.id if existing_job else None,
        )

    selected_shelf = _select_first_available_shelf(shelf_occupancies)
    if selected_shelf is None:
        raise NoAvailableLocationError(
            f"No available shelf for warehouse zone {zone}, rack {rack}"
        )

    location = shelf_locations[selected_shelf]
    now = datetime.utcnow()
    inbound_item.condition_grade = grade
    inbound_item.updated_at = now

    if existing_job is None:
        putaway_job = PutawayJob(
            inbound_item_id=inbound_item.id,
            location_id=location.id,
        )
        session.add(putaway_job)
    else:
        putaway_job = existing_job
        putaway_job.location_id = location.id
        putaway_job.status = PutawayStatus.WAITING
        putaway_job.completed_at = None
        putaway_job.cleared_at = None
        putaway_job.updated_at = now

    session.flush()
    return LocationAssignment(
        putaway_job=putaway_job,
        location=location,
    )


def _lock_zone_rack(session: Session, zone: str, rack: str) -> None:
    lock_key = f"putaway:{zone}:{rack}"
    session.exec(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(:lock_key, 0)"
            ")"
        ).bindparams(lock_key=lock_key)
    )


def _get_or_create_location(
    session: Session,
    zone: str,
    rack: str,
    shelf: str,
) -> Location:
    location = session.exec(
        select(Location)
        .where(
            Location.zone == zone,
            Location.rack == rack,
            Location.shelf == shelf,
        )
        .with_for_update()
    ).first()
    if location is not None:
        return location

    location = Location(
        zone=zone,
        rack=rack,
        shelf=shelf,
        barcode=build_location_barcode(zone, rack, shelf),
    )
    session.add(location)
    session.flush()
    return location


def _count_location_occupancy(
    session: Session,
    location: Location,
    current_putaway_job_id: UUID | None,
) -> int:
    aggregate_quantity = session.exec(
        select(func.coalesce(func.sum(Inventory.quantity), 0)).where(
            Inventory.location_id == location.id
        )
    ).one()
    used_item_quantity = session.exec(
        select(func.count())
        .select_from(InventoryUsedItem)
        .where(
            InventoryUsedItem.location_id == location.id,
            InventoryUsedItem.status.in_(
                [
                    UsedInventoryStatus.AVAILABLE,
                    UsedInventoryStatus.RESERVED,
                ]
            ),
        )
    ).one()

    putaway_statuses = [PutawayStatus.WAITING]
    if location.zone == "C":
        putaway_statuses.append(PutawayStatus.COMPLETED)

    putaway_statement = (
        select(func.count())
        .select_from(PutawayJob)
        .where(
            PutawayJob.location_id == location.id,
            PutawayJob.status.in_(putaway_statuses),
        )
    )
    if current_putaway_job_id is not None:
        putaway_statement = putaway_statement.where(
            PutawayJob.id != current_putaway_job_id
        )
    pending_quantity = session.exec(putaway_statement).one()

    return int(aggregate_quantity) + int(used_item_quantity) + int(
        pending_quantity
    )


def _select_first_available_shelf(
    shelf_occupancies: dict[str, int],
) -> str | None:
    for shelf_number in range(1, SHELF_COUNT_PER_RACK + 1):
        shelf = str(shelf_number)
        if (
            shelf in shelf_occupancies
            and shelf_occupancies[shelf] < SHELF_CAPACITY
        ):
            return shelf
    return None
