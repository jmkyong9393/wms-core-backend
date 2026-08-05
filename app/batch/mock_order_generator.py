import argparse
import logging
import os
import time

from sqlmodel import Session

from app.core.database import engine
from app.services.demo_inventory_service import (
    ensure_demo_outbound_inventory,
)
from app.services.mock_order_generator_service import (
    create_mock_outbound_order,
)


logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 1.0


# 데모용 Mock 출고 주문을 일정 간격으로 반복 생성한다.
def run_mock_order_generator(
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    max_orders: int | None = None,
    target_isbn: str | None = None,
) -> None:
    """
    데모 전용 신간·중고 재고를 자동 보충한 뒤 PENDING 주문을 생성한다.

    max_orders가 None이면 무한 실행한다.
    max_orders는 생성 시도 횟수가 아니라 실제 생성된 주문 건수다.
    """
    if interval_seconds <= 0:
        raise ValueError(
            "interval_seconds must be greater than zero"
        )

    if max_orders is not None and max_orders <= 0:
        raise ValueError(
            "max_orders must be greater than zero"
        )

    created_count = 0

    logger.info(
        "Mock outbound order generator started. "
        "interval_seconds=%s max_orders=%s",
        interval_seconds,
        max_orders,
    )

    try:
        while (
            max_orders is None
            or created_count < max_orders
        ):
            loop_started_at = time.monotonic()

            with Session(engine) as session:
                try:
                    # 우선 실제 판매 가능 재고에서 주문을 생성한다.
                    result = create_mock_outbound_order(
                        session,
                        target_isbn=target_isbn,
                    )

                    # ISBN을 지정하지 않았고 실재고 후보가 없을 때만
                    # 시연용 재고를 보충해 fallback 주문을 만든다.
                    if result is None and target_isbn is None:
                        ensure_result = (
                            ensure_demo_outbound_inventory(session)
                        )

                        if (
                            ensure_result.added_new_stock_quantity > 0
                            or ensure_result.added_used_lpn_quantity > 0
                        ):
                            logger.info(
                                "Demo inventory replenished. "
                                "added_new_stock_quantity=%s "
                                "added_used_lpn_quantity=%s",
                                ensure_result.added_new_stock_quantity,
                                ensure_result.added_used_lpn_quantity,
                            )

                        result = create_mock_outbound_order(
                            session,
                            target_isbn=(
                                ensure_result.demo_book.isbn
                            ),
                        )

                    if result is None:
                        logger.info(
                            "No demo inventory is available. "
                            "Mock order was not created."
                        )
                    else:
                        session.commit()
                        created_count += 1

                        logger.info(
                            "Mock order created. "
                            "order_id=%s order_item_id=%s "
                            "source=%s book_id=%s "
                            "condition_grade=%s total_price=%s "
                            "created_count=%s",
                            result.order_id,
                            result.order_item_id,
                            result.source,
                            result.book_id,
                            (
                                result.condition_grade.value
                                if result.condition_grade is not None
                                else None
                            ),
                            result.total_price,
                            created_count,
                        )

                except Exception:
                    session.rollback()
                    logger.exception(
                        "Mock order generation failed. "
                        "The transaction was rolled back."
                    )

            if (
                max_orders is not None
                and created_count >= max_orders
            ):
                break

            # DB 처리 시간을 제외한 나머지 시간만 대기해 약 1초 주기를 맞춘다.
            elapsed_seconds = (
                time.monotonic() - loop_started_at
            )
            sleep_seconds = max(
                0,
                interval_seconds - elapsed_seconds,
            )
            time.sleep(sleep_seconds)

    except KeyboardInterrupt:
        logger.info(
            "Mock outbound order generator stopped "
            "by keyboard interrupt."
        )

    logger.info(
        "Mock outbound order generator finished. "
        "created_count=%s",
        created_count,
    )


# CLI 실행 옵션을 읽는다.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate mock outbound orders "
            "for demo and E2E testing."
        )
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(
            os.getenv(
                "MOCK_ORDER_INTERVAL_SECONDS",
                DEFAULT_INTERVAL_SECONDS,
            )
        ),
        help="Order generation interval in seconds.",
    )
    parser.add_argument(
        "--max-orders",
        type=int,
        default=None,
        help=(
            "Number of orders to create. "
            "Omit this option for infinite execution."
        ),
    )

    parser.add_argument(
        "--isbn",
        type=str,
        default=None,
        help=(
            "ISBN of the book to use for mock order generation. "
            "If omitted, selects sellable real inventory first."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    run_mock_order_generator(
        interval_seconds=args.interval_seconds,
        max_orders=args.max_orders,
        target_isbn=args.isbn,
    )


if __name__ == "__main__":
    main()