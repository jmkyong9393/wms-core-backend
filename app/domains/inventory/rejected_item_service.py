from datetime import datetime

from sqlmodel import Session, select

from app.models.wms import Location, RejectedItem, RejectedItemStatus


def discard_all_rejected_items(
    session: Session,
) -> tuple[int, datetime]:
    rejected_items = session.exec(
        select(RejectedItem)
        .join(Location, RejectedItem.location_id == Location.id)
        .where(
            Location.zone == "C",
            RejectedItem.status == RejectedItemStatus.REJECT_HOLD,
        )
        .order_by(RejectedItem.id)
        .with_for_update(of=RejectedItem)
    ).all()
    discarded_at = datetime.utcnow()
    for rejected_item in rejected_items:
        rejected_item.status = RejectedItemStatus.DISCARDED
        rejected_item.discarded_at = discarded_at
        rejected_item.updated_at = discarded_at
        session.add(rejected_item)

    return len(rejected_items), discarded_at
