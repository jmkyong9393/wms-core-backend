from sqlalchemy import func, text
from sqlmodel import Session, select

from app.domain.warehouse_location_policy import (
    SHELF_CAPACITY,
    SHELF_COUNT_PER_RACK,
    build_location_barcode,
    rack_for_category,
    zone_for_used_grade,
)
from app.models.wms import (
    Book,
    ConditionGrade,
    Inventory,
    InventoryUsedItem,
    Location,
    RejectedItem,
    RejectedItemStatus,
    UsedInventoryStatus,
)


class NoAvailableLocationError(Exception):
    pass


NEW_STOCK_ZONE = "A"


def assign_new_stock_location(
    session: Session,
    book: Book,
    quantity: int,
) -> Location:
    if quantity > SHELF_CAPACITY:
        raise NoAvailableLocationError(
            f"New stock intake quantity exceeds shelf capacity {SHELF_CAPACITY}"
        )

    rack = rack_for_category(book.category)
    _lock_zone_rack(session, NEW_STOCK_ZONE, rack)

    existing_locations = session.exec(
        select(Location)
        .join(Inventory, Inventory.location_id == Location.id)
        .where(
            Inventory.book_id == book.id,
            Location.zone == NEW_STOCK_ZONE,
            Location.is_active.is_(True),
        )
        .order_by(Inventory.created_at, Inventory.id)
        .with_for_update(of=Inventory)
    ).all()
    for existing_location in existing_locations:
        occupancy = _count_location_occupancy(
            session=session,
            location=existing_location,
        )
        if occupancy + quantity <= SHELF_CAPACITY:
            return existing_location

    return _assign_available_location(
        session=session,
        zone=NEW_STOCK_ZONE,
        rack=rack,
        required_capacity=quantity,
    )


def assign_graded_inventory_location(
    session: Session,
    book: Book,
    grade: ConditionGrade,
) -> Location:
    zone = zone_for_used_grade(grade)
    rack = rack_for_category(book.category)
    _lock_zone_rack(session, zone, rack)
    return _assign_available_location(
        session=session,
        zone=zone,
        rack=rack,
        required_capacity=1,
    )


def _assign_available_location(
    session: Session,
    zone: str,
    rack: str,
    required_capacity: int,
) -> Location:
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
        )

    selected_shelf = _select_first_available_shelf(
        shelf_occupancies,
        required_capacity,
    )
    if selected_shelf is None:
        raise NoAvailableLocationError(
            f"No available shelf for warehouse zone {zone}, rack {rack}"
        )

    return shelf_locations[selected_shelf]


def _lock_zone_rack(session: Session, zone: str, rack: str) -> None:
    lock_key = f"inventory-location:{zone}:{rack}"
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

    rejected_quantity = session.exec(
        select(func.count())
        .select_from(RejectedItem)
        .where(
            RejectedItem.location_id == location.id,
            RejectedItem.status == RejectedItemStatus.REJECT_HOLD,
        )
    ).one()

    return int(aggregate_quantity) + int(used_item_quantity) + int(
        rejected_quantity
    )


def _select_first_available_shelf(
    shelf_occupancies: dict[str, int],
    required_capacity: int,
) -> str | None:
    for shelf_number in range(1, SHELF_COUNT_PER_RACK + 1):
        shelf = str(shelf_number)
        if (
            shelf in shelf_occupancies
            and shelf_occupancies[shelf] + required_capacity <= SHELF_CAPACITY
        ):
            return shelf
    return None
