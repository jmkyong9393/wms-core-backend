import sys

sys.stdout.reconfigure(encoding="utf-8")

from sqlmodel import Session, text
from app.core.database import engine


def query_auto_po():
    with Session(engine) as session:
        query = text("""
            SELECT o.id, o.customer_name, o.total_price, oi.book_id, oi.quantity, o.created_at
            FROM orders o 
            JOIN order_items oi ON o.id = oi.order_id 
            WHERE o.type = 'AUTO_PO'
            ORDER BY o.created_at DESC
        """)
        result = session.execute(query)
        rows = result.fetchall()

        print("==========================================")
        print(f"[DB Search Result] Total {len(rows)} AUTO_PO items found.")
        print("==========================================\n")

        if not rows:
            print("아직 발주서가 생성되지 않았습니다. K8s Job을 먼저 실행해 주세요!")
            return

        for row in rows:
            print(f"[Date: {row[5].strftime('%Y-%m-%d %H:%M:%S')}]")
            print(f"[Publisher: {row[1]}]")
            print(f"Book ID: {row[3]}")
            print(f"Quantity: {row[4]} (Total Price: {row[2]:,.0f} KRW)")
            print("-" * 40)


if __name__ == "__main__":
    query_auto_po()
