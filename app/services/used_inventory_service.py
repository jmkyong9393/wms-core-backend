from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
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
    PutawayJob,
    PutawayStatus,
    ReturnJob,
    ReturnJobStatus,
)
from app.schemas.hitl import HITLReasonCode
from app.schemas.inspection_inventory import RejectionDisposition
from app.services.location_assignment_service import (
    NoAvailableLocationError,
    assign_putaway_location,
)


AdmissionDecision = Literal["APPROVE", "REJECT"]


@dataclass(frozen=True)
class InspectionPutawayResult:
    return_job_id: UUID
    inbound_item_id: UUID
    decision: AdmissionDecision
    condition_grade: ConditionGrade
    lpn_barcode: str
    putaway_job_id: UUID
    putaway_status: PutawayStatus
    location_id: UUID
    location_barcode: str
    putaway_changed: bool


def assign_inspected_item_putaway(
    session: Session,
    return_job_id: UUID,
    decision: AdmissionDecision,
    ubci_score: Decimal | None,
    defects: list[dict[str, Any]],
    admin_decision_code: HITLReasonCode | None = None,
    final_grade: ConditionGrade | None = None,
    rejection_disposition: RejectionDisposition | None = None,
) -> InspectionPutawayResult:
    return_job = session.get(ReturnJob, return_job_id)
    if return_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Return job not found",
        )

    _save_admin_decision_logs(
        session=session,
        return_job=return_job,
        admin_decision_code=admin_decision_code,
        final_grade=final_grade,
        rejection_disposition=rejection_disposition,
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
    if inbound_job.status != InboundStatus.CHECKING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Inbound job cannot assign putaway from {inbound_job.status}",
        )
    if return_job.status != ReturnJobStatus.PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Return job cannot assign putaway from {return_job.status}",
        )

    condition_grade = _resolve_condition_grade(
        decision=decision,
        ubci_score=ubci_score,
        defects=defects,
        final_grade=final_grade,
        rejection_disposition=rejection_disposition,
    )
    book = session.get(Book, inbound_item.book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Book linked to inbound item was not found",
        )

    existing_putaway = session.exec(
        select(PutawayJob).where(
            PutawayJob.inbound_item_id == inbound_item.id
        )
    ).first()
    previous_grade = inbound_item.condition_grade
    previous_location_id = (
        existing_putaway.location_id if existing_putaway is not None else None
    )
    previous_status = (
        existing_putaway.status if existing_putaway is not None else None
    )
    try:
        assignment = assign_putaway_location(
            session=session,
            inbound_item=inbound_item,
            book=book,
            grade=condition_grade,
        )
    except NoAvailableLocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    putaway_changed = (
        existing_putaway is None
        or previous_location_id != assignment.location.id
        or previous_grade != condition_grade
        or previous_status != PutawayStatus.WAITING
    )
    return InspectionPutawayResult(
        return_job_id=return_job.id,
        inbound_item_id=inbound_item.id,
        decision=decision,
        condition_grade=condition_grade,
        lpn_barcode=inbound_item.lpn_barcode,
        putaway_job_id=assignment.putaway_job.id,
        putaway_status=assignment.putaway_job.status,
        location_id=assignment.location.id,
        location_barcode=assignment.location.barcode,
        putaway_changed=putaway_changed,
    )


def _resolve_condition_grade(
    decision: AdmissionDecision,
    ubci_score: Decimal | None,
    defects: list[dict[str, Any]],
    final_grade: ConditionGrade | None,
    rejection_disposition: RejectionDisposition | None,
) -> ConditionGrade:
    if decision == "REJECT":
        return ConditionGrade.REJECT
    if rejection_disposition is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Approved inspection cannot use rejection disposition",
        )
    if final_grade in {ConditionGrade.NEW, ConditionGrade.REJECT}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Approved inspection requires a sellable used grade",
        )
    if final_grade is not None:
        return final_grade
    if ubci_score is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Approved inspection requires UBCI score or final grade",
        )

    grade = determine_condition_grade(ubci_score, defects)
    if grade == ConditionGrade.REJECT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approved inspection result maps to REJECT condition grade",
        )
    return grade


def _save_admin_decision_logs(
    session: Session,
    return_job: ReturnJob,
    admin_decision_code: HITLReasonCode | None,
    final_grade: ConditionGrade | None,
    rejection_disposition: RejectionDisposition | None,
) -> None:
    if all(
        value is None
        for value in (
            admin_decision_code,
            final_grade,
            rejection_disposition,
        )
    ):
        return

    updated_logs = dict(return_job.agent_logs or {})
    updated_logs["admin_decision_code"] = (
        admin_decision_code.value if admin_decision_code is not None else None
    )
    updated_logs["final_grade"] = (
        final_grade.value if final_grade is not None else None
    )
    updated_logs["rejection_disposition"] = rejection_disposition
    return_job.agent_logs = updated_logs
    session.add(return_job)


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
