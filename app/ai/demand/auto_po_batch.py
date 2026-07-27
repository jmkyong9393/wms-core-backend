import logging
import uuid
from typing import List, Dict, Any
from collections import defaultdict
from sqlmodel import Session, text
from app.core.database import engine
from app.models.wms import OrderType, OrderStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def fetch_books_needing_restock(session: Session) -> List[Dict[str, Any]]:
    logger.info("DB에서 출고 이력이 있으며 실질 재고가 안전재고 미달인 도서를 조회합니다...")
    # SQL 단에서 실질 재고(물리+입고예정)와 안전재고(주간판매량*2, 최소 10권)를 비교하여 
    # 발주가 필요한 도서만 메모리로 가져옵니다. (메모리 최적화)
    # 또한 주간 판매량이 1권 이상인 도서만 발주 대상에 포함합니다. (악성 재고 무한 발주 방지)
    query = text("""
        WITH WeeklySales AS (
            SELECT 
                oi.book_id,
                COALESCE(SUM(oi.quantity), 0) as weekly_sales
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            WHERE o.created_at >= NOW() - INTERVAL '7 days'
              AND o.type = 'B2B_ORDER'
            GROUP BY oi.book_id
        ),
        CurrentInventory AS (
            SELECT
                book_id,
                COALESCE(SUM(quantity), 0) as total_stock
            FROM inventory
            GROUP BY book_id
        ),
        PendingPOs AS (
            SELECT
                oi.book_id,
                COALESCE(SUM(oi.quantity), 0) as pending_po_qty
            FROM order_items oi
            JOIN orders o ON oi.order_id = o.id
            -- 입고 전까지의 모든 진행 상태 포함 (PENDING, PICKING, SHIPPED)
            WHERE o.type = 'AUTO_PO' AND o.status IN ('PENDING', 'PICKING', 'SHIPPED')
            GROUP BY oi.book_id
        )
        SELECT 
            b.id as book_id,
            b.title,
            b.publisher,
            b.base_price,
            COALESCE(c.total_stock, 0) as total_stock,
            COALESCE(w.weekly_sales, 0) as weekly_sales,
            COALESCE(p.pending_po_qty, 0) as pending_po_qty
        FROM books b
        JOIN WeeklySales w ON b.id = w.book_id -- INNER JOIN으로 주간 판매량이 있는 도서만 필터링
        LEFT JOIN CurrentInventory c ON b.id = c.book_id
        LEFT JOIN PendingPOs p ON b.id = p.book_id
        WHERE w.weekly_sales > 0 
          AND (COALESCE(c.total_stock, 0) + COALESCE(p.pending_po_qty, 0)) < GREATEST(10, w.weekly_sales * 2)
    """)
    result = session.execute(query)
    data = []
    for row in result:
        data.append({
            "book_id": str(row[0]),
            "title": row[1],
            "publisher": row[2] or "UNKNOWN_PUBLISHER",
            "base_price": float(row[3]) if row[3] else 0.0,
            "total_stock": int(row[4]),
            "weekly_sales": int(row[5]),
            "pending_po_qty": int(row[6])
        })
    return data

def run_auto_po_batch():
    logger.info("=== Auto-PO (자동 발주) 배치 작업 시작 ===")
    
    with Session(engine) as session:
        try:
            books_to_order = fetch_books_needing_restock(session)
            
            if not books_to_order:
                logger.info("안전재고 미달 도서가 없습니다. 발주를 생성하지 않습니다.")
                return
                
            # 출판사별 발주서(PO) 분리를 위한 Grouping
            po_by_publisher = defaultdict(list)
            
            for stat in books_to_order:
                book_id = stat["book_id"]
                title = stat["title"]
                publisher = stat["publisher"]
                total_stock = stat["total_stock"]
                weekly_sales = stat["weekly_sales"]
                base_price = stat["base_price"]
                pending_po_qty = stat["pending_po_qty"]
                
                effective_stock = total_stock + pending_po_qty
                safety_stock = max(10, weekly_sales * 2)
                
                order_quantity = max(10, safety_stock - effective_stock)
                final_price = order_quantity * base_price
                
                logger.info(f"[{publisher}] '{title}' 재고 부족 감지! 실질재고: {effective_stock} < 안전재고: {safety_stock}. {order_quantity}권 발주 필요.")
                
                po_by_publisher[publisher].append({
                    "book_id": book_id,
                    "quantity": order_quantity,
                    "unit_price": base_price,
                    "final_price": final_price
                })
            
            # 대량 Insert(Bulk Insert)를 위한 리스트 준비
            orders_to_insert = []
            order_items_to_insert = []
            
            for publisher, items in po_by_publisher.items():
                po_order_id = str(uuid.uuid4())
                total_po_price = sum(item["final_price"] for item in items)
                
                # 출판사 단위의 발주서(Order) 생성 (customer_name에 출판사명 저장)
                orders_to_insert.append({
                    "id": po_order_id,
                    "customer_name": publisher, 
                    "type": OrderType.AUTO_PO.value,
                    "total_price": total_po_price,
                    "status": OrderStatus.PENDING.value
                })
                
                for item in items:
                    order_items_to_insert.append({
                        "id": str(uuid.uuid4()),
                        "order_id": po_order_id,
                        "book_id": item["book_id"],
                        "quantity": item["quantity"],
                        "unit_price": item["unit_price"],
                        "final_price": item["final_price"]
                    })
            
            # Bulk Insert 실행 (DB I/O 최적화)
            logger.info(f"총 {len(orders_to_insert)}개의 출판사에 대한 발주서와 {len(order_items_to_insert)}개의 품목을 생성합니다...")
            
            insert_order_query = text("""
                INSERT INTO orders (id, customer_name, type, total_price, status, logistics_center)
                VALUES (:id, :customer_name, :type, :total_price, :status, 'CENTER_A')
            """)
            session.execute(insert_order_query, orders_to_insert)
            
            insert_item_query = text("""
                INSERT INTO order_items (id, order_id, book_id, quantity, unit_price, final_price)
                VALUES (:id, :order_id, :book_id, :quantity, :unit_price, :final_price)
            """)
            session.execute(insert_item_query, order_items_to_insert)
                
            session.commit()
            logger.info(f"=== Auto-PO 배치 작업 완료 (발주서: {len(orders_to_insert)}건, 품목: {len(order_items_to_insert)}건) ===")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Auto-PO 배치 작업 중 오류 발생: {e}")
            raise

if __name__ == "__main__":
    run_auto_po_batch()
