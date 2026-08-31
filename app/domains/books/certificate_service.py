import json
from typing import NoReturn

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.wms import Book, InboundItem, InventoryUsedItem, ReturnJob
from app.domains.books.schemas.certificate import CertificateBookDetail, CertificateResponse


def extract_report_summary(final_report: str | None) -> str | None:
    if final_report is None:
        return None

    normalized_report = final_report.strip()
    if not normalized_report:
        return None

    try:
        parsed_report = json.loads(normalized_report)
    except (json.JSONDecodeError, TypeError):
        return normalized_report

    if not isinstance(parsed_report, dict):
        return normalized_report

    for key in ("message", "result"):
        value = parsed_report.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _raise_certificate_not_found() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Certificate not found",
    )


def get_certificate_by_token(
    session: Session,
    token: str,
) -> CertificateResponse:
    inbound_item = session.exec(
        select(InboundItem).where(
            InboundItem.certificate_token == token,
        )
    ).first()
    if inbound_item is None:
        _raise_certificate_not_found()

    return_job = session.exec(
        select(ReturnJob)
        .where(ReturnJob.inbound_item_id == inbound_item.id)
        .order_by(ReturnJob.updated_at.desc(), ReturnJob.id.desc())
    ).first()
    if return_job is None:
        _raise_certificate_not_found()

    inventory_item = session.exec(
        select(InventoryUsedItem).where(
            InventoryUsedItem.return_job_id == return_job.id,
        )
    ).first()
    if inventory_item is None:
        _raise_certificate_not_found()

    book = session.get(Book, inbound_item.book_id)
    report_summary = extract_report_summary(return_job.final_report)
    if (
        book is None
        or return_job.condition_grade is None
        or return_job.ubci_score is None
        or report_summary is None
    ):
        _raise_certificate_not_found()

    return CertificateResponse(
        book=CertificateBookDetail(
            isbn=book.isbn,
            title=book.title,
            publisher=book.publisher,
            cover_image_url=book.cover_image_url,
        ),
        condition_grade=return_job.condition_grade,
        ubci_score=return_job.ubci_score,
        report_summary=report_summary,
        inspected_at=return_job.updated_at,
    )
