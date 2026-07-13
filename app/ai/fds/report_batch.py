import logging
import pandas as pd
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def fetch_recent_returns() -> List[Dict[str, Any]]:
    """DB에서 분석 대상이 되는 최근 반품 데이터를 가져오는 함수"""
    logger.info("DB에서 최근 반품 데이터를 조회합니다...")
    # TO-DO: SQLModel / SQLAlchemy 조회 로직 구현
    # 향후 DB(orders, return_jobs JOIN)에서 아래와 같은 Group By 형태로 추출하게 됩니다.
    dummy_data = [
        # 악성 유저 (반품 4회, 평균 도서 상태 최악, 고액 환불)
        {"user_id": "고객사_A", "return_count": 4, "avg_ubci_score": 25.5, "total_refund_amount": 550000, "return_reasons": ["파손", "완전파손", "파손", "단순변심"]},
        # 정상 유저 (반품 1회, 도서 상태 양호)
        {"user_id": "고객사_B", "return_count": 1, "avg_ubci_score": 95.0, "total_refund_amount": 20000, "return_reasons": ["오주문"]},
        # 주의 유저 (반품 빈도 높으나 금액/상태는 애매한 경우)
        {"user_id": "고객사_C", "return_count": 5, "avg_ubci_score": 85.0, "total_refund_amount": 600000, "return_reasons": ["단순변심", "단순변심", "단순변심", "단순변심", "오주문"]}
    ]
    return dummy_data

def save_fds_report(results: List[Dict[str, Any]]):
    """탐지된 블랙컨슈머(FDS) 결과를 DB에 저장하거나 알림을 발송하는 함수"""
    logger.info(f"총 {len(results)}건의 이상거래 의심 내역이 보고되었습니다.")
    # TO-DO: FDS 리포트 테이블(SQL)에 결과 INSERT 또는 Slack API 알림
    for res in results:
        logger.warning(f"🚨 이상거래 탐지: {res}")

def generate_weekly_insights(raw_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pandas를 활용하여 관리자 대시보드용 주간 통계 지표를 계산합니다."""
    logger.info("대시보드용 주간 인사이트 분석을 시작합니다 (Pandas)...")
    
    df = pd.DataFrame(raw_data)
    
    if df.empty:
        return {}
        
    insights = {
        "total_returns_this_week": int(df["return_count"].sum()),
        "avg_ubci_score": float(df["avg_ubci_score"].mean()),
        "top_return_reasons": df.explode("return_reasons")["return_reasons"].value_counts().head(3).to_dict()
    }
    return insights

def save_insight_report(insights: Dict[str, Any]):
    """계산된 주간 통계 지표를 DB에 적재하는 함수"""
    logger.info("생성된 주간 인사이트 리포트:")
    for key, value in insights.items():
        logger.info(f" - {key}: {value}")
    # TO-DO: weekly_insights 통계 전용 테이블에 INSERT

def main():
    from app.ai.fds.detector import detect_black_consumers
    from datetime import datetime
    import sys
    
    logger.info(f"🚀 FDS Report Batch 시작 시간: {datetime.now()}")
    try:
        # 1. 데이터 추출
        raw_data = fetch_recent_returns()
        
        # 2. [기존] FDS 이상 탐지 분석 및 적재
        suspicious_records = detect_black_consumers(raw_data)
        save_fds_report(suspicious_records)
        
        # 3. [신규] 대시보드용 주간 통계 생성 및 적재
        insights = generate_weekly_insights(raw_data)
        save_insight_report(insights)
        
    except Exception as e:
        logger.error(f"❌ Batch 실행 중 에러 발생: {e}")
        sys.exit(1) # K8s Job 실패 처리를 위해 exit code 1 반환
        
    logger.info("✅ FDS Report Batch가 성공적으로 종료되었습니다.")
    sys.exit(0)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
