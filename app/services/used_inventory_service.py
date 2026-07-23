from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlmodel import Session, select

from app.domain.ubci_grade_policy import determine_condition_grade
from app.models.wms import (
    Book,
    ConditionGrade,
    InboundItem,
    InboundJob,
    InboundStatus,
    InboundType,
    InspectionMode,
    InventoryLog,
    InventoryTransactionType,
    InventoryUsedItem,
    Location,
    ReturnJob,
    ReturnJobStatus,
    UsedInventoryStatus,
)


AdmissionDecision = Literal["APPROVE", "REJECT"]


@dataclass(frozen=True)
class InventoryAdmissionResult:
    return_job_id: UUID
    inbound_item_id: UUID
    decision: AdmissionDecision
    condition_grade: ConditionGrade
    lpn_barcode: str
    inventory_used_item_id: UUID | None
    inventory_status: UsedInventoryStatus | None
    inventory_changed: bool


def admit_inspected_item(
    session: Session,
    return_job_id: UUID,
    decision: AdmissionDecision,
    ubci_score: Decimal,
    defects: list[dict[str, Any]],
    location_barcode: str | None,
) -> InventoryAdmissionResult:
    return_job = session.get(ReturnJob, return_job_id)
    if return_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Return job not found",
        )
    if return_job.inbound_item_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Return job is not linked to an inbound item",
        )

    inbound_item = session.exec(
        select(InboundItem)
        .where(InboundItem.id == return_job.inbound_item_id)
        .with_for_update()
    ).first()
    if inbound_item is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound item linked to return job was not found",
        )
    if inbound_item.lpn_barcode is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound item does not have an LPN barcode",
        )
    if inbound_item.book_id != return_job.book_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Return job and inbound item reference different books",
        )

    inbound_job = session.exec(
        select(InboundJob)
        .where(InboundJob.id == inbound_item.inbound_job_id)
        .with_for_update()
    ).first()
    if inbound_job is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inbound job linked to inbound item was not found",
        )

    _validate_inspection_mode(return_job, inbound_job)

    existing_inventory = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.return_job_id == return_job.id
        )
    ).first()
    if inbound_job.status == InboundStatus.COMPLETED:
        return _replay_completed_admission(
            session=session,
            return_job=return_job,
            inbound_item=inbound_item,
            existing_inventory=existing_inventory,
            requested_decision=decision,
            requested_score=ubci_score,
            requested_defects=defects,
            requested_location_barcode=location_barcode,
        )

    if inbound_job.status != InboundStatus.CHECKING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Inbound job cannot be completed from status {inbound_job.status}",
        )
    if return_job.status != ReturnJobStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Return job cannot admit inventory from status {return_job.status}",
        )

    if decision == "REJECT":
        inbound_job.status = InboundStatus.COMPLETED
        inbound_job.updated_at = datetime.utcnow()
        session.add(inbound_job)
        return InventoryAdmissionResult(
            return_job_id=return_job.id,
            inbound_item_id=inbound_item.id,
            decision="REJECT",
            condition_grade=ConditionGrade.REJECT,
            lpn_barcode=inbound_item.lpn_barcode,
            inventory_used_item_id=None,
            inventory_status=None,
            inventory_changed=False,
        )

    condition_grade = determine_condition_grade(ubci_score, defects)
    if condition_grade == ConditionGrade.REJECT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approved inspection result maps to REJECT condition grade",
        )

    location = session.exec(
        select(Location).where(Location.barcode == location_barcode)
    ).first()
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location barcode not found",
        )
    if not location.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Location is inactive",
        )

    stocked_at = datetime.utcnow()
    inventory_used_item = InventoryUsedItem(
        book_id=inbound_item.book_id,
        location_id=location.id,
        return_job_id=return_job.id,
        lpn_barcode=inbound_item.lpn_barcode,
        ubci_score=ubci_score,
        condition_grade=condition_grade,
        status=UsedInventoryStatus.AVAILABLE,
        stocked_at=stocked_at,
    )
    session.add(inventory_used_item)
    session.flush()

    transaction_type = (
        InventoryTransactionType.RETURN_RESTOCK
        if inbound_job.inbound_type == InboundType.CUSTOMER_RETURN
        else InventoryTransactionType.INBOUND
    )
    session.add(
        InventoryLog(
            transaction_type=transaction_type,
            book_id=inbound_item.book_id,
            condition_grade=condition_grade,
            quantity_change=1,
            target_lpn=inbound_item.lpn_barcode,
            picked_location=location.barcode,
        )
    )
    session.exec(
        update(Book)
        .where(Book.id == inbound_item.book_id)
        .values(
            virtual_stock=Book.virtual_stock + 1,
            updated_at=stocked_at,
        )
        .execution_options(synchronize_session=False)
    )

    inbound_job.status = InboundStatus.COMPLETED
    inbound_job.updated_at = stocked_at
    session.add(inbound_job)

    return InventoryAdmissionResult(
        return_job_id=return_job.id,
        inbound_item_id=inbound_item.id,
        decision="APPROVE",
        condition_grade=condition_grade,
        lpn_barcode=inbound_item.lpn_barcode,
        inventory_used_item_id=inventory_used_item.id,
        inventory_status=inventory_used_item.status,
        inventory_changed=True,
    )


def _validate_inspection_mode(
    return_job: ReturnJob,
    inbound_job: InboundJob,
) -> None:
    expected_inbound_type = {
        InspectionMode.RETURN: InboundType.CUSTOMER_RETURN,
        InspectionMode.USED_PURCHASE: InboundType.USED_PURCHASE,
    }[return_job.mode]

    if inbound_job.inbound_type != expected_inbound_type:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inspection mode does not match inbound type",
        )


def _replay_completed_admission(
    session: Session,
    return_job: ReturnJob,
    inbound_item: InboundItem,
    existing_inventory: InventoryUsedItem | None,
    requested_decision: AdmissionDecision,
    requested_score: Decimal,
    requested_defects: list[dict[str, Any]],
    requested_location_barcode: str | None,
) -> InventoryAdmissionResult:
    completed_decision: AdmissionDecision = (
        "APPROVE" if existing_inventory is not None else "REJECT"
    )
    if requested_decision != completed_decision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inspection result was already applied with another decision",
        )

    if existing_inventory is None:
        return InventoryAdmissionResult(
            return_job_id=return_job.id,
            inbound_item_id=inbound_item.id,
            decision="REJECT",
            condition_grade=ConditionGrade.REJECT,
            lpn_barcode=inbound_item.lpn_barcode,
            inventory_used_item_id=None,
            inventory_status=None,
            inventory_changed=False,
        )

    requested_grade = determine_condition_grade(
        requested_score,
        requested_defects,
    )
    existing_location = session.get(Location, existing_inventory.location_id)
    if (
        requested_grade != existing_inventory.condition_grade
        or requested_score != existing_inventory.ubci_score
        or existing_location is None
        or requested_location_barcode != existing_location.barcode
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Inspection result was already applied with different data",
        )

    return InventoryAdmissionResult(
        return_job_id=return_job.id,
        inbound_item_id=inbound_item.id,
        decision="APPROVE",
        condition_grade=existing_inventory.condition_grade,
        lpn_barcode=existing_inventory.lpn_barcode,
        inventory_used_item_id=existing_inventory.id,
        inventory_status=existing_inventory.status,
        inventory_changed=False,
    )
