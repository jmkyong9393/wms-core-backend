import logging

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session

from app.models.wms import (
    InboundItem,
    InventoryUsedItem,
    ReturnJob,
)

from app.schemas.inspection_inventory import (
    InspectionInventoryRequest,
    InspectionInventoryResponse,
)
from app.schemas.label import LabelPrintStatus
from app.services.used_inventory_service import apply_inspected_item_result
from app.services.dynamic_pricing_service import execute_dynamic_pricing
from app.services.label_printer_service import (
    send_zpl_to_label_printer,
)
from app.services.zpl_label_service import build_ubci_label_zpl
from app.domains.books.certificate_service import extract_report_summary


router = APIRouter()

logger = logging.getLogger(__name__)


def _try_print_ubci_label(
    inbound_item: InboundItem,
    inventory_item: InventoryUsedItem,
    return_job: ReturnJob,
) -> tuple[LabelPrintStatus, str | None]:
    """
    검수 승인·재고 편입이 DB에 저장된 뒤 UBCI QR 라벨 전송을 시도한다.

    소비자가 QR을 스캔했을 때 공개 품질보증서를 조회할 수 있도록,
    Agent 검수 보고서가 확정된 경우에만 출력한다.
    프린터 오류는 이미 완료된 검수·재고 편입 트랜잭션을 롤백하지 않는다.
    """
    if extract_report_summary(return_job.final_report) is None:
        return (
            LabelPrintStatus.SKIPPED,
            (
                "공개 품질보증서가 아직 준비되지 않아 "
                "UBCI 라벨 출력을 보류했습니다."
            ),
        )

    try:
        if inbound_item.certificate_token is None:
            raise RuntimeError(
                "Inbound item does not have a certificate token"
            )

        zpl = build_ubci_label_zpl(
            lpn_barcode=inventory_item.lpn_barcode,
            certificate_token=inbound_item.certificate_token,
            condition_grade=inventory_item.condition_grade.value,
            ubci_score=inventory_item.ubci_score,
        )
        print_result = send_zpl_to_label_printer(zpl)

        if print_result.skipped:
            return LabelPrintStatus.SKIPPED, None

        return LabelPrintStatus.SENT, None

    except Exception:
        logger.exception(
            "Failed to print UBCI label. "
            "inventory_used_item_id=%s",
            inventory_item.id,
        )
        return (
            LabelPrintStatus.FAILED,
            "UBCI 라벨 출력에 실패했습니다. 수동 출력이 필요합니다.",
        )

@router.post(
    "/inspection-results",
    response_model=InspectionInventoryResponse,
    operation_id="applyInspectionInventoryResult",
    summary="검수 결과 기반 LPN 재고 또는 폐기 대기 편입",
    description=(
        "AI 검수 결과를 입고 품목에 적용합니다. 승인 결과는 UBCI 정책으로 "
        "등급을 계산하여 B Zone 판매 가능 단품 재고에 즉시 편입하고, 반려 "
        "결과는 C Zone의 폐기 대기 레코드로 분리합니다."
    ),
    responses={
        404: {"description": "검수 작업을 찾을 수 없음"},
        409: {"description": "검수·입고 상태 충돌 또는 가용 로케이션 없음"},
    },
)
def apply_inspection_inventory_result(
    request: InspectionInventoryRequest,
    session: Session = Depends(get_session),
) -> InspectionInventoryResponse:
    try:
        result = apply_inspected_item_result(
            session=session,
            return_job_id=request.return_job_id,
            decision=request.decision,
            ubci_score=request.ubci_score,
            defects=request.defects,
            admin_decision_code=request.admin_decision_code,
            final_grade=request.final_grade,
            rejection_disposition=request.rejection_disposition,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    if (
        result.decision == "APPROVE"
        and result.inventory_changed
        and result.inventory_used_item_id is not None
    ):
        try:
            execute_dynamic_pricing(
                session=session,
                lpn_barcode=result.lpn_barcode,
            )
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "Dynamic pricing failed after inventory admission. "
                "lpn_barcode=%s",
                result.lpn_barcode,
            )

    label_print_status = LabelPrintStatus.SKIPPED
    label_print_error = None

    # 승인된 신규 판매 가능 단품에만 UBCI 라벨을 한 번 자동 출력한다.
    # 반려 건과 동일 검수 결과 재요청은 중복 출력하지 않는다.
    if (
        result.decision == "APPROVE"
        and result.inventory_changed
        and result.inventory_used_item_id is not None
    ):
        try:
            inbound_item = session.get(
                InboundItem,
                result.inbound_item_id,
            )
            inventory_item = session.get(
                InventoryUsedItem,
                result.inventory_used_item_id,
            )
            return_job = session.get(
                ReturnJob,
                result.return_job_id,
            )

            if (
                inbound_item is None
                or inventory_item is None
                or return_job is None
            ):
                logger.error(
                    "UBCI label print data was not found. "
                    "inbound_item_id=%s inventory_used_item_id=%s",
                    result.inbound_item_id,
                    result.inventory_used_item_id,
                )
                label_print_status = LabelPrintStatus.FAILED
                label_print_error = (
                    "UBCI 라벨 출력 데이터를 찾을 수 없습니다. "
                    "수동 출력이 필요합니다."
                )
            else:
                label_print_status, label_print_error = (
                    _try_print_ubci_label(
                        inbound_item=inbound_item,
                        inventory_item=inventory_item,
                        return_job=return_job,
                    )
                )
        except Exception:
            # 이 시점의 검수 결과·재고 편입·가격 산정 커밋은 이미 끝났다.
            # 라벨 후처리 실패가 API 전체 실패가 되지 않게 한다.
            session.rollback()
            logger.exception(
                "Failed to prepare UBCI label printing. "
                "return_job_id=%s",
                result.return_job_id,
            )
            label_print_status = LabelPrintStatus.FAILED
            label_print_error = (
                "UBCI 라벨 출력 처리 중 오류가 발생했습니다. "
                "수동 출력이 필요합니다."
            )

    return InspectionInventoryResponse(
        return_job_id=result.return_job_id,
        inbound_item_id=result.inbound_item_id,
        decision=result.decision,
        condition_grade=result.condition_grade,
        lpn_barcode=result.lpn_barcode,
        location_id=result.location_id,
        location_barcode=result.location_barcode,
        inventory_used_item_id=result.inventory_used_item_id,
        rejected_item_id=result.rejected_item_id,
        inventory_changed=result.inventory_changed,
        label_print_status=label_print_status,
        label_print_error=label_print_error,
    )
