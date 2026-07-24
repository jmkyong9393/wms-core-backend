import logging
import pandas as pd
from typing import List, Dict, Any
from sqlmodel import Session, text
import uuid
from app.core.database import engine
from app.models.wms import FdsReport, WeeklyInsight

logger = logging.getLogger(__name__)

def fetch_recent_returns(session: Session) -> List[Dict[str, Any]]:
    logger.info("DB에서 최근 반품 데이터를 조회합니다...")
    query = text("""
    SELECT 
        o.customer_id,
        o.customer_name,
        COUNT(CASE WHEN r.created_at >= NOW() - INTERVAL '30 days' THEN 1 END) AS returns_last_30d,
        COUNT(CASE WHEN r.created_at >= NOW() - INTERVAL '90 days' THEN 1 END) AS returns_last_90d,
        COUNT(CASE WHEN r.created_at >= NOW() - INTERVAL '7 days' THEN 1 END) AS weekly_return_count,
        AVG(r.ubci_score) AS avg_ubci_score,
        SUM(oi.final_price) AS total_refund_amount,
        STRING_AGG(r.final_report, ', ') AS final_report
    FROM return_jobs r
    JOIN orders o ON r.order_id = o.id
    JOIN order_items oi ON r.order_id = oi.order_id AND r.book_id = oi.book_id
    WHERE r.created_at >= NOW() - INTERVAL '90 days' AND r.status = 'APPROVED'
    GROUP BY o.customer_id, o.customer_name;
    """)
    result = session.execute(query)
    data = []
    for row in result:
        data.append({
            "customer_id": str(row[0]),
            "customer_name": row[1],
            "returns_last_30d": row[2] or 0,
            "returns_last_90d": row[3] or 0,
            "weekly_return_count": row[4] or 0,
            "avg_ubci_score": float(row[5]) if row[5] is not None else 100.0,
            "total_refund_amount": float(row[6]) if row[6] is not None else 0.0,
            "final_report": row[7] or ""
        })
    return data

def fetch_inventory_stats(session: Session) -> Dict[str, Any]:
    logger.info("DB에서 현재 센터 총 재고 현황을 조회합니다...")
    query = text("""
        SELECT COALESCE(SUM(quantity), 0) FROM inventory;
    """)
    total_books = session.execute(query).scalar()
    
    query_low = text("""
        SELECT COUNT(*) FROM inventory WHERE quantity < 50;
    """)
    low_stock = session.execute(query_low).scalar()
    
    query_scrap = text("""
        SELECT COUNT(*) FROM inventory_used_items WHERE condition_grade = 'REJECT';
    """)
    scrap = session.execute(query_scrap).scalar()
    
    return {
        "total_books_in_stock": total_books,
        "low_stock_items_count": low_stock,
        "scrap_books_count": scrap
    }

def fetch_order_stats(session: Session) -> Dict[str, Any]:
    logger.info("DB에서 이번 주 발주 및 출고 내역을 조회합니다...")
    outbound = session.execute(text("SELECT COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL '7 days' AND type = 'B2B_ORDER'")).scalar()
    inbound_po = session.execute(text("SELECT COUNT(*) FROM orders WHERE created_at >= NOW() - INTERVAL '7 days' AND type = 'AUTO_PO'")).scalar()
    inbound_received = session.execute(text("SELECT COUNT(*) FROM inbound_jobs WHERE created_at >= NOW() - INTERVAL '7 days' AND status = 'RECEIVED'")).scalar()
    
    return {
        "weekly_outbound_orders": outbound,
        "weekly_inbound_po": inbound_po,
        "total_inbound_books_received": inbound_received
    }

def fetch_location_hotspots(session: Session) -> Dict[str, int]:
    query = text("""
    SELECT loc.zone AS location_zone, COUNT(*) AS count
    FROM return_jobs r
    JOIN order_items oi ON r.order_id = oi.order_id AND r.book_id = oi.book_id
    JOIN locations loc ON oi.location_id = loc.id
    WHERE r.created_at >= NOW() - INTERVAL '7 days' AND r.status = 'APPROVED'
    GROUP BY loc.zone
    ORDER BY count DESC LIMIT 2;
    """)
    result = session.execute(query)
    return {row[0]: row[1] for row in result}

def fetch_logistics_hotspots(session: Session) -> Dict[str, int]:
    query = text("""
    SELECT o.logistics_center, COUNT(*) AS count
    FROM return_jobs r
    JOIN orders o ON r.order_id = o.id
    WHERE r.created_at >= NOW() - INTERVAL '7 days' AND r.status = 'APPROVED'
    GROUP BY o.logistics_center
    ORDER BY count DESC LIMIT 2;
    """)
    result = session.execute(query)
    return {row[0]: row[1] for row in result}

def fetch_defective_publishers(session: Session) -> Dict[str, int]:
    query = text("""
    SELECT b.publisher, COUNT(*) AS count
    FROM return_jobs r
    JOIN books b ON r.book_id = b.id
    WHERE r.created_at >= NOW() - INTERVAL '7 days' AND r.status = 'APPROVED'
    GROUP BY b.publisher
    ORDER BY count DESC LIMIT 2;
    """)
    result = session.execute(query)
    return {row[0]: row[1] for row in result}

def save_fds_report(session: Session, results: List[Dict[str, Any]]):
    logger.info(f"총 {len(results)}건의 이상거래 의심 내역이 보고되었습니다.")
    for res in results:
        cid = res["customer_id"]
        new_score = res["fraud_score"]
        new_reason = res["fraud_reason"]
        
        # 1. 기존 적발 이력 조회
        existing = session.execute(
            text("SELECT id, fraud_score FROM fds_reports WHERE customer_id = :cid"),
            {"cid": cid}
        ).fetchone()
        
        if not existing:
            # 최초 적발: 신규 INSERT
            logger.warning(f"🚨 신규 이상거래 탐지: {res}")
            report = FdsReport(
                id=uuid.uuid4(),
                customer_id=cid,
                fraud_score=new_score,
                fraud_reason=new_reason
            )
            session.add(report)
        else:
            old_id = existing[0]
            old_score = existing[1]
            
            if new_score > old_score:
                # 2. 이미 적발되었으나 위험도가 더 상향(악화)된 경우: UPDATE 및 detected_at 갱신
                logger.warning(f"🚨 이상거래 위험도 상향 갱신 (점수: {old_score} -> {new_score}): {res}")
                session.execute(
                    text("""
                        UPDATE fds_reports 
                        SET fraud_score = :score, 
                            fraud_reason = :reason, 
                            detected_at = CURRENT_TIMESTAMP, 
                            updated_at = CURRENT_TIMESTAMP 
                        WHERE id = :id
                    """),
                    {"score": new_score, "reason": new_reason, "id": old_id}
                )
            else:
                # 3. 동일 점수이거나 점수가 낮아진 경우 무시 (최초 적발일 detected_at 보존)
                logger.info(f"✅ 기존 탐지 이력 유지 (점수 악화 없음) - {cid}")

def generate_weekly_insights(
    session: Session,
    raw_return_data: List[Dict[str, Any]], 
    inventory_stats: Dict[str, Any], 
    order_stats: Dict[str, Any]
) -> Dict[str, Any]:
    logger.info("대시보드용 주간 인사이트 통합 분석을 시작합니다 (SQL Push-down)...")
    
    if not raw_return_data:
        return {}
        
    total_returns_this_week = sum(record.get("weekly_return_count", 0) for record in raw_return_data)
    saved_labor_cost_krw = int(total_returns_this_week * 90 * 9860 / 3600)
    
    top_defective_publishers = fetch_defective_publishers(session)
    location_hotspots = fetch_location_hotspots(session)
    logistics_hotspots = fetch_logistics_hotspots(session)
        
    weekly_outbound = order_stats.get("weekly_outbound_orders", 0)
    predicted_returns = int(weekly_outbound * 0.03)
        
    from datetime import datetime
    current_week = f"{datetime.now().year}-W{datetime.now().isocalendar()[1]}"
    
    insights = {
        "report_week": current_week,
        "saved_labor_cost_krw": saved_labor_cost_krw,
        "top_defective_publishers": top_defective_publishers,
        "location_hotspots": location_hotspots,
        "logistics_hotspots": logistics_hotspots,
        "predicted_returns": predicted_returns
    }
    
    return insights

def save_insight_report(session: Session, insights: Dict[str, Any]):
    if not insights:
        return
    logger.info("생성된 주간 인사이트 리포트:")
    for key, value in insights.items():
        logger.info(f" - {key}: {value}")
        
    # 동일 주차 리포트가 있으면 멱등성 위해 삭제 후 재삽입
    session.execute(text("DELETE FROM weekly_insights WHERE report_week = :wk"), {"wk": insights["report_week"]})
    
    report = WeeklyInsight(
        id=uuid.uuid4(),
        report_week=insights["report_week"],
        saved_labor_cost_krw=insights["saved_labor_cost_krw"],
        top_defective_publishers=insights["top_defective_publishers"],
        location_hotspots=insights["location_hotspots"],
        logistics_hotspots=insights["logistics_hotspots"],
        predicted_returns=insights["predicted_returns"]
    )
    session.add(report)

def main():
    from app.ai.reporting.detector import detect_black_consumers, fetch_fds_policies
    from datetime import datetime
    import sys
    
    logger.info(f"🚀 FDS Report Batch 시작 시간: {datetime.now()}")
    
    with Session(engine) as session:
        try:
            config = fetch_fds_policies(session)
            raw_return_data = fetch_recent_returns(session)
            inventory_stats = fetch_inventory_stats(session)
            order_stats = fetch_order_stats(session)
            
            suspicious_records = detect_black_consumers(raw_return_data, config)
            insights = generate_weekly_insights(session, raw_return_data, inventory_stats, order_stats)
            
            logger.info("DB 트랜잭션을 시작합니다 (BEGIN)...")
            save_fds_report(session, suspicious_records)
            save_insight_report(session, insights)
            
            session.commit()
            logger.info("모든 데이터가 멱등성을 보장하며 성공적으로 적재되었습니다 (COMMIT).")
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Batch 실행 중 에러 발생, 롤백 처리: {e}")
            sys.exit(1)
            
    logger.info("✅ FDS Report Batch가 성공적으로 종료되었습니다.")
    sys.exit(0)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
