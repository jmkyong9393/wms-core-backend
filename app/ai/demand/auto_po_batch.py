import logging
import uuid
from typing import List, Dict, Any
from sqlmodel import Session, text
from app.core.database import engine
from app.models.wms import OrderType, OrderStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def fetch_inventory_and_sales(session: Session) -> List[Dict[str, Any]]:
    logger.info("DB에서 도서별 재고 및 최근 7일 출고량을 조회합니다...")
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
        )
        SELECT 
            b.id as book_id,
            b.title,
            b.base_price,
            COALESCE(c.total_stock, 0) as total_stock,
            COALESCE(w.weekly_sales, 0) as weekly_sales
        FROM books b
        LEFT JOIN CurrentInventory c ON b.id = c.book_id
        LEFT JOIN WeeklySales w ON b.id = w.book_id
    """)
    result = session.execute(query)
    data = []
    for row in result:
        data.append({
            "book_id": str(row[0]),
            "title": row[1],
            "base_price": float(row[2]) if row[2] else 0.0,
            "total_stock": int(row[3]),
            "weekly_sales": int(row[4])
        })
    return data

def run_auto_po_batch():
    logger.info("=== Auto-PO (자동 발주) 배치 작업 시작 ===")
    
    with Session(engine) as session:
        try:
            book_stats = fetch_inventory_and_sales(session)
            
            po_items_to_create = []
            total_po_price = 0.0
            
            for stat in book_stats:
                book_id = stat["book_id"]
                title = stat["title"]
                total_stock = stat["total_stock"]
                weekly_sales = stat["weekly_sales"]
                base_price = stat["base_price"]
                
                # 안전재고 로직: 최근 7일 판매량의 2배 (약 2주치 재고 확보), 최소 10권
                safety_stock = max(10, weekly_sales * 2)
                
                if total_stock < safety_stock:
                    # 부족한 만큼 발주하되, 최소 10권 단위로 발주하도록 보정
                    order_quantity = max(10, safety_stock - total_stock)
                    logger.info(f"[{title}] 재고 부족 감지! 현재고: {total_stock} < 안전재고: {safety_stock}. {order_quantity}권 발주 필요.")
                    
                    final_price = order_quantity * base_price
                    total_po_price += final_price
                    
                    po_items_to_create.append({
                        "book_id": book_id,
                        "quantity": order_quantity,
                        "unit_price": base_price,
                        "final_price": final_price
                    })
                    
            if not po_items_to_create:
                logger.info("안전재고 미달 도서가 없습니다. 발주를 생성하지 않습니다.")
                return
                
            # Create a single PO order for all missing items
            po_order_id = str(uuid.uuid4())
            logger.info(f"Auto-PO 주문 생성 중... Order ID: {po_order_id}, 품목 수: {len(po_items_to_create)}건, 총 금액: {total_po_price}")
            
            # orders 테이블 삽입
            insert_order_query = text("""
                INSERT INTO orders (id, type, total_price, status, logistics_center)
                VALUES (:id, :type, :total_price, :status, 'CENTER_A')
            """)
            session.execute(insert_order_query, {
                "id": po_order_id,
                "type": OrderType.AUTO_PO.value,
                "total_price": total_po_price,
                "status": OrderStatus.PENDING.value
            })
            
            # order_items 테이블 삽입
            for item in po_items_to_create:
                order_item_id = str(uuid.uuid4())
                insert_item_query = text("""
                    INSERT INTO order_items (id, order_id, book_id, quantity, unit_price, final_price)
                    VALUES (:id, :order_id, :book_id, :quantity, :unit_price, :final_price)
                """)
                session.execute(insert_item_query, {
                    "id": order_item_id,
                    "order_id": po_order_id,
                    "book_id": item["book_id"],
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"],
                    "final_price": item["final_price"]
                })
                
            session.commit()
            logger.info(f"=== Auto-PO 배치 작업 완료 (PO 생성 수: 1건, 품목 수: {len(po_items_to_create)}건) ===")
            
        except Exception as e:
            session.rollback()
            logger.error(f"Auto-PO 배치 작업 중 오류 발생: {e}")
            raise

if __name__ == "__main__":
    run_auto_po_batch()
