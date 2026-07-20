import logging
import pandas as pd
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def fetch_recent_returns() -> List[Dict[str, Any]]:
    """
    DB에서 분석 대상이 되는 최근 반품 데이터를 가져오는 함수
    (조건부 집계 및 JOIN 활용하여 OOM 방지)
    
    [실제 적용될 SQL 쿼리 예시]
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
    WHERE r.created_at >= NOW() - INTERVAL '90 days'
    GROUP BY o.customer_id, o.customer_name;
    """
    logger.info("DB에서 최근 반품 데이터를 조회합니다...")
    # TO-DO: 위 쿼리를 활용한 SQLModel / SQLAlchemy 로직 구현
    dummy_data = [
        # 악성 유저 (최근 30일 4회, 90일 5회)
        {
            "customer_id": "550e8400-e29b-41d4-a716-446655440000", "customer_name": "홍길동",
            "returns_last_30d": 4, "returns_last_90d": 5, "weekly_return_count": 2, 
            "avg_ubci_score": 25.5, "total_refund_amount": 550000, 
            "final_report": "파손,완전파손,파손,단순변심"
        },
        # 정상 유저
        {
            "customer_id": "123e4567-e89b-12d3-a456-426614174000", "customer_name": "이영희",
            "returns_last_30d": 1, "returns_last_90d": 1, "weekly_return_count": 1, 
            "avg_ubci_score": 95.0, "total_refund_amount": 20000, 
            "final_report": "오주문"
        },
        # 주의 유저
        {
            "customer_id": "987e6543-e21b-34d5-c678-426614174999", "customer_name": "김철수",
            "returns_last_30d": 2, "returns_last_90d": 6, "weekly_return_count": 3, 
            "avg_ubci_score": 85.0, "total_refund_amount": 600000, 
            "final_report": "단순변심,단순변심,파손,파손,오주문"
        }
    ]
    return dummy_data

def fetch_inventory_stats() -> Dict[str, Any]:
    """DB(books, inventory)에서 현재 물류센터의 전체 도서 재고 상황을 가져오는 함수"""
    logger.info("DB에서 현재 센터 총 재고 현황을 조회합니다...")
    # TO-DO: SQLModel / SQLAlchemy 조회 로직 구현
    # dummy inventory data
    return {
        "total_books_in_stock": 145000,       # 총 보관 중인 도서 수량
        "low_stock_items_count": 12,          # 안전재고 미달(발주 필요) 도서 종류 수
        "scrap_books_count": 45               # 폐기 대기(SCRAP 등급) 도서 수량
    }

def fetch_order_stats() -> Dict[str, Any]:
    """DB(orders, inbound_jobs)에서 이번 주 발주(Inbound) 및 출고(Outbound) 건수를 가져오는 함수"""
    logger.info("DB에서 이번 주 발주 및 출고 내역을 조회합니다...")
    # TO-DO: SQLModel / SQLAlchemy 조회 로직 구현
    # dummy order data
    return {
        "weekly_outbound_orders": 4520,       # 주간 출고 처리 건수
        "weekly_inbound_po": 18,              # 주간 출판사 자동 발주(PO) 전송 건수
        "total_inbound_books_received": 1200  # 주간 새로 입고된 새 책 수량
    }


def fetch_location_hotspots() -> Dict[str, int]:
    """
    DB에서 직접 GROUP BY 연산으로 내부 파손 핫스팟을 집계하여 반환합니다 (SQL Push-down)
    
    [실제 적용될 SQL 쿼리 예시]
    SELECT loc.zone AS location_zone, COUNT(*) AS count
    FROM return_jobs r
    JOIN order_items oi ON r.order_id = oi.order_id AND r.book_id = oi.book_id
    JOIN locations loc ON oi.location_id = loc.id
    WHERE r.created_at >= NOW() - INTERVAL '7 days'
    GROUP BY loc.zone
    ORDER BY count DESC LIMIT 2;
    """
    return {"Zone_A": 2, "Zone_C": 1}

def fetch_logistics_hotspots() -> Dict[str, int]:
    """
    DB에서 직접 GROUP BY 연산으로 외부 배송 핫스팟을 집계하여 반환합니다 (SQL Push-down)
    
    [실제 적용될 SQL 쿼리 예시]
    SELECT o.logistics_center, COUNT(*) AS count
    FROM return_jobs r
    JOIN orders o ON r.order_id = o.id
    WHERE r.created_at >= NOW() - INTERVAL '7 days'
    GROUP BY o.logistics_center
    ORDER BY count DESC LIMIT 2;
    """
    return {"서초_3센터": 2, "경기_광주센터": 1}

def fetch_defective_publishers() -> Dict[str, int]:
    """
    DB에서 직접 GROUP BY 연산으로 불량 출판사를 집계하여 반환합니다 (SQL Push-down)
    
    [실제 적용될 SQL 쿼리 예시]
    SELECT b.publisher, COUNT(*) AS count
    FROM return_jobs r
    JOIN books b ON r.book_id = b.id
    WHERE r.created_at >= NOW() - INTERVAL '7 days'
    GROUP BY b.publisher
    ORDER BY count DESC LIMIT 2;
    """
    return {"A출판사": 2, "B비전북스": 1}

def save_fds_report(results: List[Dict[str, Any]]):
    """탐지된 블랙컨슈머(FDS) 결과를 DB에 저장하거나 알림을 발송하는 함수"""
    logger.info(f"총 {len(results)}건의 이상거래 의심 내역이 보고되었습니다.")
    # TO-DO: FDS 리포트 테이블(SQL)에 결과 INSERT 또는 Slack API 알림
    for res in results:
        logger.warning(f"🚨 이상거래 탐지: {res}")

def generate_weekly_insights(
    raw_return_data: List[Dict[str, Any]], 
    inventory_stats: Dict[str, Any], 
    order_stats: Dict[str, Any]
) -> Dict[str, Any]:
    """대시보드용 주간 통계 지표를 통합 계산합니다. (Pandas 제거, SQL 연산 활용)"""
    logger.info("대시보드용 주간 인사이트 통합 분석을 시작합니다 (SQL Push-down)...")
    
    # 1. 반품 관련 통계
    if not raw_return_data:
        return {}
        
    total_returns_this_week = sum(record.get("weekly_return_count", 0) for record in raw_return_data)
    
    # 재무적 임팩트(Cost Saved) 산출
    saved_labor_cost_krw = int(total_returns_this_week * 90 * 9860 / 3600)
    
    # 품질 핫스팟(Quality Trend) 분석 - Pandas value_counts 제거 후 DB 직접 쿼리로 대체
    top_defective_publishers = fetch_defective_publishers()
    location_hotspots = fetch_location_hotspots()
    logistics_hotspots = fetch_logistics_hotspots()
        
    # [신규 C] 예측(Forecasting) 로직
    # 이번 주 출고량의 3%가 다음 주 반품으로 돌아온다고 가정한 Rule-based 예측
    weekly_outbound = order_stats.get("weekly_outbound_orders", 0)
    predicted_returns = int(weekly_outbound * 0.03)
        
    # 2. 통합 리포트 스냅샷 생성 (ERD weekly_insights 스키마 1:1 매칭)
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

def save_insight_report(insights: Dict[str, Any]):
    """계산된 주간 통계 지표를 DB에 적재하는 함수"""
    logger.info("생성된 주간 인사이트 리포트:")
    for key, value in insights.items():
        logger.info(f" - {key}: {value}")
    # TO-DO: weekly_insights 통계 전용 테이블에 INSERT

def main():
    from app.ai.reporting.detector import detect_black_consumers, fetch_fds_policies
    from datetime import datetime
    import sys
    
    logger.info(f"🚀 FDS Report Batch 시작 시간: {datetime.now()}")
    try:
        # 1. Config 조회 및 데이터 추출 (발주, 재고, 반품)
        config = fetch_fds_policies()
        raw_return_data = fetch_recent_returns()
        inventory_stats = fetch_inventory_stats()
        order_stats = fetch_order_stats()
        
        # 2. [기존] FDS 이상 탐지 분석
        suspicious_records = detect_black_consumers(raw_return_data, config)
        
        # 3. [신규] 대시보드용 WMS 통합 주간 통계 생성 (SQL Push-down)
        insights = generate_weekly_insights(raw_return_data, inventory_stats, order_stats)
        
        # 4. 트랜잭션(Transaction) 및 멱등성(Idempotency) 방어 로직
        logger.info("DB 트랜잭션을 시작합니다 (BEGIN)...")
        try:
            # TO-DO: SQLAlchemy Session Start
            # ON CONFLICT DO UPDATE 로직을 적용하여 멱등성 확보
            save_fds_report(suspicious_records)
            save_insight_report(insights)
            
            # TO-DO: session.commit()
            logger.info("모든 데이터가 멱등성을 보장하며 성공적으로 적재되었습니다 (COMMIT).")
        except Exception as db_err:
            # TO-DO: session.rollback()
            logger.error("DB 적재 중 에러 발생, 전체 롤백 처리합니다 (ROLLBACK).")
            raise db_err
        
    except Exception as e:
        logger.error(f"❌ Batch 실행 중 에러 발생: {e}")
        sys.exit(1) # K8s Job 실패 처리를 위해 exit code 1 반환
        
    logger.info("✅ FDS Report Batch가 성공적으로 종료되었습니다.")
    sys.exit(0)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
