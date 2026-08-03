import logging
from typing import Any

from sqlmodel import Session, text

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import engine


logger = logging.getLogger(__name__)

SAFETY_STOCK_RESTOCK_PROPOSAL_TASK_NAME = (
    "app.worker.process_safety_stock_restock_proposal"
)


def fetch_books_needing_restock(
    session: Session,
) -> list[dict[str, Any]]:
    """
    최근 판매 이력이 있고 실질 가용 재고와 진행 중 AUTO_PO 수량의 합이
    안전재고보다 적은 도서만 조회한다.

    실제 추천안 생성과 주문 생성은 여기서 하지 않는다.
    """
    query = text(
        """
        WITH weekly_sales AS (
            SELECT
                order_items.book_id,
                COALESCE(SUM(order_items.quantity), 0) AS weekly_sales
            FROM order_items
            JOIN orders
                ON orders.id = order_items.order_id
            WHERE orders.created_at >= NOW() - INTERVAL '7 days'
              AND orders.type = 'B2B_ORDER'
              AND orders.status = 'SHIPPED'
            GROUP BY order_items.book_id
        ),
        new_stock AS (
            SELECT
                book_id,
                COALESCE(
                    SUM(quantity - reserved_quantity),
                    0
                ) AS available_quantity
            FROM inventory
            GROUP BY book_id
        ),
        used_stock AS (
            SELECT
                book_id,
                COUNT(*) AS available_quantity
            FROM inventory_used_items
            WHERE status = 'AVAILABLE'
            GROUP BY book_id
        ),
        pending_auto_po AS (
            SELECT
                order_items.book_id,
                COALESCE(SUM(order_items.quantity), 0)
                    AS pending_quantity
            FROM order_items
            JOIN orders
                ON orders.id = order_items.order_id
            WHERE orders.type = 'AUTO_PO'
              AND orders.status IN (
                  'PENDING',
                  'PICKING',
                  'SHIPPED'
              )
            GROUP BY order_items.book_id
        )
        SELECT
            books.id AS book_id,
            books.title,
            COALESCE(weekly_sales.weekly_sales, 0)
                AS weekly_sales,
            (
                COALESCE(new_stock.available_quantity, 0)
                + COALESCE(used_stock.available_quantity, 0)
            ) AS current_stock,
            COALESCE(pending_auto_po.pending_quantity, 0)
                AS pending_auto_po_quantity,
            GREATEST(
                10,
                COALESCE(weekly_sales.weekly_sales, 0) * 2
            ) AS safety_stock_quantity
        FROM books
        JOIN weekly_sales
            ON weekly_sales.book_id = books.id
        LEFT JOIN new_stock
            ON new_stock.book_id = books.id
        LEFT JOIN used_stock
            ON used_stock.book_id = books.id
        LEFT JOIN pending_auto_po
            ON pending_auto_po.book_id = books.id
        WHERE (
            COALESCE(new_stock.available_quantity, 0)
            + COALESCE(used_stock.available_quantity, 0)
            + COALESCE(pending_auto_po.pending_quantity, 0)
        ) < GREATEST(
            10,
            COALESCE(weekly_sales.weekly_sales, 0) * 2
        )
        """
    )

    rows = session.execute(query).fetchall()

    return [
        {
            "book_id": str(row.book_id),
            "title": row.title,
            "weekly_sales": int(row.weekly_sales),
            "current_stock": int(row.current_stock),
            "pending_auto_po_quantity": int(
                row.pending_auto_po_quantity
            ),
            "safety_stock_quantity": int(
                row.safety_stock_quantity
            ),
        }
        for row in rows
    ]


def run_auto_po_batch() -> None:
    """
    안전재고 부족 도서를 탐지하고, 도서별로 Celery Restock Agent 작업을
    등록한다. 주문과 재고는 관리자 승인 API에서만 변경된다.
    """
    if settings.AUTO_PO_TENANT_ID is None:
        raise RuntimeError(
            "AUTO_PO_TENANT_ID must be configured "
            "before running the auto-PO batch."
        )

    logger.info(
        "=== Safety-stock Restock proposal batch started ==="
    )

    with Session(engine) as session:
        books_to_restock = fetch_books_needing_restock(
            session,
        )

    if not books_to_restock:
        logger.info(
            "No books are below the safety-stock threshold."
        )
        return

    for book in books_to_restock:
        task = celery_app.send_task(
            SAFETY_STOCK_RESTOCK_PROPOSAL_TASK_NAME,
            args=[
                str(settings.AUTO_PO_TENANT_ID),
                book["book_id"],
                book["safety_stock_quantity"],
            ],
        )

        logger.info(
            "Safety-stock Restock Agent task queued. "
            "task_id=%s book_id=%s title=%s "
            "current_stock=%s pending_auto_po_quantity=%s "
            "safety_stock_quantity=%s",
            task.id,
            book["book_id"],
            book["title"],
            book["current_stock"],
            book["pending_auto_po_quantity"],
            book["safety_stock_quantity"],
        )


if __name__ == "__main__":
    run_auto_po_batch()