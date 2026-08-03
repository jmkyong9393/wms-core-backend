import uuid
from datetime import datetime, timedelta
from sqlmodel import Session, SQLModel, text, select
from app.core.database import engine
from app.models.wms import (
    Tenant, Book, Location, Order, OrderItem, ReturnJob, Inventory,
    InboundItem, InboundJob, FdsPolicy, RejectedItem,
    StandardSize, InboundType, InboundStatus, ConditionGrade, BookCategory,
    OrderType, OrderStatus, ReturnJobStatus, InspectionMode
)

def seed_db():
    with Session(engine) as session:
        # 0. Clean up transaction and master tables to ensure idempotence
        session.execute(text("TRUNCATE TABLE rejected_items, inbound_items, inbound_jobs, return_jobs, order_items, orders, inventory, weekly_insights, fds_reports, fds_policies, books, locations CASCADE"))
        session.commit()
        
        # 1. Tenant
        tenant = session.exec(select(Tenant).where(Tenant.code == "AIVLE_WMS")).first()
        if not tenant:
            tenant = Tenant(id=uuid.uuid4(), code="AIVLE_WMS", name="AIVLE WMS")
            session.add(tenant)
            session.flush()
        
        # 2. FdsPolicy
        session.execute(text("DELETE FROM fds_policies"))
        policies = [
            FdsPolicy(policy_key="MAX_RETURN_30D", policy_value=3),
            FdsPolicy(policy_key="MIN_UBCI_SCORE", policy_value=30.0),
            FdsPolicy(policy_key="MAX_RETURN_90D", policy_value=5),
            FdsPolicy(policy_key="MAX_REFUND_AMT", policy_value=500000)
        ]
        session.add_all(policies)

        # 3. Books
        book1 = Book(
            id=uuid.uuid4(),
            title="Book A",
            publisher="A출판사",
            category=BookCategory.NOVEL,
            base_price=15000,
        )
        book2 = Book(
            id=uuid.uuid4(),
            title="Book B",
            publisher="B비전북스",
            category=BookCategory.SCIENCE_TECHNOLOGY,
            base_price=20000,
        )
        session.add_all([book1, book2])
        
        # 4. Locations
        def get_or_create_location(zone, rack, shelf, barcode):
            loc = session.exec(select(Location).where(Location.barcode == barcode)).first()
            if not loc:
                loc = Location(id=uuid.uuid4(), zone=zone, rack=rack, shelf=shelf, barcode=barcode)
                session.add(loc)
                session.flush()
            return loc

        loc1 = get_or_create_location("A", "1", "1", "A-1-1")
        loc2 = get_or_create_location("B", "2", "2", "B-2-2")
        loc3 = get_or_create_location("C", "1", "1", "C-1-1")
        session.commit()
        
        # 5. Inventory (for fetch_inventory_stats & auto_po_batch)
        # Auto-PO 테스트를 위해 재고 수량을 안전재고(10권) 미만으로 설정합니다.
        inv1 = Inventory(id=uuid.uuid4(), book_id=book1.id, location_id=loc1.id, quantity=3)
        inv2 = Inventory(id=uuid.uuid4(), book_id=book2.id, location_id=loc2.id, quantity=2)
        session.add_all([inv1, inv2])
        
        # 6. InboundJobs (for fetch_order_stats)
        inbound1 = InboundJob(id=uuid.uuid4(), inbound_type=InboundType.NEW_STOCK, status=InboundStatus.RECEIVED, supplier_name="A출판사")
        inbound2 = InboundJob(id=uuid.uuid4(), inbound_type=InboundType.NEW_STOCK, status=InboundStatus.RECEIVED, supplier_name="B비전북스")
        session.add_all([inbound1, inbound2])
        
        # 7. Orders (Logistics Hotspots & Outbound stats)
        # 7-1 악성 유저 (홍길동)
        bad_customer_id = uuid.uuid4()
        order_bad = Order(
            id=uuid.uuid4(), customer_id=bad_customer_id, customer_name="홍길동", 
            type=OrderType.B2B_ORDER, total_price=550000, status=OrderStatus.SHIPPED, logistics_center="서초_3센터"
        )
        
        # 7-2 주의 유저 (김철수)
        watch_customer_id = uuid.uuid4()
        order_watch = Order(
            id=uuid.uuid4(), customer_id=watch_customer_id, customer_name="김철수", 
            type=OrderType.B2B_ORDER, total_price=600000, status=OrderStatus.SHIPPED, logistics_center="경기_광주센터"
        )
        
        # 7-3 정상 유저 (이영희)
        good_customer_id = uuid.uuid4()
        order_good = Order(
            id=uuid.uuid4(), customer_id=good_customer_id, customer_name="이영희", 
            type=OrderType.B2B_ORDER, total_price=20000, status=OrderStatus.SHIPPED, logistics_center="서초_3센터"
        )
        
        # 대량 출고 건수 반영용 더미 오더들 (weekly_outbound_orders)
        dummy_orders = [Order(id=uuid.uuid4(), type=OrderType.B2B_ORDER, total_price=10000, status=OrderStatus.SHIPPED) for _ in range(50)]
        session.add_all([order_bad, order_watch, order_good] + dummy_orders)
        session.commit()
        
        # 8. Order Items
        oi_bad1 = OrderItem(id=uuid.uuid4(), order_id=order_bad.id, book_id=book1.id, location_id=loc1.id, quantity=1, unit_price=15000, final_price=150000)
        oi_bad2 = OrderItem(id=uuid.uuid4(), order_id=order_bad.id, book_id=book2.id, location_id=loc2.id, quantity=1, unit_price=20000, final_price=400000)
        
        oi_watch = OrderItem(id=uuid.uuid4(), order_id=order_watch.id, book_id=book1.id, location_id=loc2.id, quantity=1, unit_price=15000, final_price=600000)
        
        oi_good = OrderItem(id=uuid.uuid4(), order_id=order_good.id, book_id=book2.id, location_id=loc1.id, quantity=1, unit_price=20000, final_price=20000)
        
        session.add_all([oi_bad1, oi_bad2, oi_watch, oi_good])
        session.commit()
        
        # 9. Return Jobs (for FDS & Hotspots)
        now = datetime.now()
        
        # 홍길동: 최근 30일 4회 반품, 90일 5회. 평균 UBCI 매우 낮음 (파손)
        bad_returns = []
        for i in range(4):
            rj = ReturnJob(
                id=uuid.uuid4(), tenant_id=tenant.id, order_id=order_bad.id, book_id=book1.id, 
                mode=InspectionMode.RETURN, status=ReturnJobStatus.APPROVED,
                ubci_score=25.5, final_report="파손", created_at=now - timedelta(days=2)
            )
            bad_returns.append(rj)
        
        # 김철수: 최근 30일 2회, 90일 6회. 환불 금액 과다. UBCI는 양호(단순변심)
        watch_returns = []
        for i in range(2):
            rj = ReturnJob(
                id=uuid.uuid4(), tenant_id=tenant.id, order_id=order_watch.id, book_id=book1.id, 
                mode=InspectionMode.RETURN, status=ReturnJobStatus.APPROVED,
                ubci_score=85.0, final_report="단순변심", created_at=now - timedelta(days=5)
            )
            watch_returns.append(rj)
            
        # 이영희: 정상(오주문 1회)
        good_return = ReturnJob(
            id=uuid.uuid4(), tenant_id=tenant.id, order_id=order_good.id, book_id=book2.id, 
            mode=InspectionMode.RETURN, status=ReturnJobStatus.APPROVED,
            ubci_score=95.0, final_report="오주문", created_at=now - timedelta(days=1)
        )
        
        session.add_all(bad_returns + watch_returns + [good_return])
        
        # 10. RejectedItem (for REJECT/SCRAP items count)
        rejected_inbound = InboundJob(
            inbound_type=InboundType.USED_PURCHASE,
            status=InboundStatus.COMPLETED,
            supplier_name="중고 매입",
        )
        session.add(rejected_inbound)
        session.flush()
        for i in range(45):
            lpn_barcode = f"LPN-REJECT-{i}"
            inbound_item = InboundItem(
                inbound_job_id=rejected_inbound.id,
                book_id=book1.id,
                quantity=1,
                lpn_barcode=lpn_barcode,
                condition_grade=ConditionGrade.REJECT,
            )
            session.add(inbound_item)
            session.flush()
            session.add(
                RejectedItem(
                    inbound_item_id=inbound_item.id,
                    book_id=book1.id,
                    location_id=loc3.id,
                    lpn_barcode=lpn_barcode,
                )
            )
        
        session.commit()
        print("✅ DB Seed Data Insertion Completed!")

if __name__ == "__main__":
    seed_db()
