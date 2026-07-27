import uuid
from datetime import datetime, timedelta
from sqlmodel import Session, SQLModel, text
from app.core.database import engine
from app.models.wms import (
    Tenant, Book, Location, Order, OrderItem, ReturnJob, Inventory,
    InventoryUsedItem, InboundJob, FdsPolicy, 
    StandardSize, InboundType, InboundStatus, ConditionGrade,
    OrderType, OrderStatus, ReturnJobStatus, InspectionMode, UsedInventoryStatus
)

def seed_db():
    with Session(engine) as session:
        # Create Tables if not exist (optional, assuming init.sql already ran)
        
        # 1. Tenant
        tenant = Tenant(id=uuid.uuid4(), code="T001", name="Test Tenant")
        session.add(tenant)
        
        # 2. FdsPolicy
        policies = [
            FdsPolicy(policy_key="MAX_RETURN_30D", policy_value=3),
            FdsPolicy(policy_key="MIN_UBCI_SCORE", policy_value=30.0),
            FdsPolicy(policy_key="MAX_RETURN_90D", policy_value=5),
            FdsPolicy(policy_key="MAX_REFUND_AMT", policy_value=500000)
        ]
        session.add_all(policies)

        # 3. Books
        book1 = Book(id=uuid.uuid4(), title="Book A", publisher="A출판사", base_price=15000)
        book2 = Book(id=uuid.uuid4(), title="Book B", publisher="B비전북스", base_price=20000)
        session.add_all([book1, book2])
        
        # 4. Locations
        loc1 = Location(id=uuid.uuid4(), zone="Zone_A", rack="R1", shelf="S1")
        loc2 = Location(id=uuid.uuid4(), zone="Zone_C", rack="R2", shelf="S2")
        session.add_all([loc1, loc2])
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
                target_location_id=loc1.id, mode=InspectionMode.RETURN, status=ReturnJobStatus.APPROVED,
                ubci_score=25.5, final_report="파손", created_at=now - timedelta(days=2)
            )
            bad_returns.append(rj)
        
        # 김철수: 최근 30일 2회, 90일 6회. 환불 금액 과다. UBCI는 양호(단순변심)
        watch_returns = []
        for i in range(2):
            rj = ReturnJob(
                id=uuid.uuid4(), tenant_id=tenant.id, order_id=order_watch.id, book_id=book1.id, 
                target_location_id=loc2.id, mode=InspectionMode.RETURN, status=ReturnJobStatus.APPROVED,
                ubci_score=85.0, final_report="단순변심", created_at=now - timedelta(days=5)
            )
            watch_returns.append(rj)
            
        # 이영희: 정상(오주문 1회)
        good_return = ReturnJob(
            id=uuid.uuid4(), tenant_id=tenant.id, order_id=order_good.id, book_id=book2.id, 
            target_location_id=loc1.id, mode=InspectionMode.RETURN, status=ReturnJobStatus.APPROVED,
            ubci_score=95.0, final_report="오주문", created_at=now - timedelta(days=1)
        )
        
        session.add_all(bad_returns + watch_returns + [good_return])
        
        # 10. InventoryUsedItem (for REJECT/SCRAP items count)
        scrap_items = [InventoryUsedItem(
            id=uuid.uuid4(), book_id=book1.id, location_id=loc1.id, lpn_barcode=f"LPN-{i}", 
            condition_grade=ConditionGrade.REJECT, status=UsedInventoryStatus.AVAILABLE
        ) for i in range(45)]
        session.add_all(scrap_items)
        
        session.commit()
        print("✅ DB Seed Data Insertion Completed!")

if __name__ == "__main__":
    seed_db()
