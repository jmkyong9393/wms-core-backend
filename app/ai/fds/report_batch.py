import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def fetch_recent_returns() -> List[Dict[str, Any]]:
    """DB에서 분석 대상이 되는 최근 반품 데이터를 가져오는 함수"""
    logger.info("DB에서 최근 반품 데이터를 조회합니다...")
    # TO-DO: SQLModel / SQLAlchemy 조회 로직 구현
    # 예시 임시 데이터
    dummy_data = [
        {"user_id": "user_001", "return_count": 4, "reason": "파손", "total_refund_amount": 150000},
        {"user_id": "user_002", "return_count": 1, "reason": "단순변심", "total_refund_amount": 20000}
    ]
    return dummy_data

def save_report(results: List[Dict[str, Any]]):
    """탐지된 결과를 DB에 저장하거나 알림을 발송하는 함수"""
    logger.info(f"총 {len(results)}건의 이상거래 의심 내역이 보고되었습니다.")
    # TO-DO: FDS 리포트 테이블(SQL)에 결과 INSERT 또는 Slack API 알림
    for res in results:
        logger.warning(f"🚨 이상거래 탐지: {res}")

def main():
    from app.ai.fds.detector import detect_black_consumers
    from datetime import datetime
    import sys
    
    logger.info(f"🚀 FDS Report Batch 시작 시간: {datetime.now()}")
    try:
        # 1. 데이터 추출
        raw_data = fetch_recent_returns()
        
        # 2. 이상 탐지 분석 (Rule-based)
        suspicious_records = detect_black_consumers(raw_data)
        
        # 3. 결과 적재 및 리포팅
        save_report(suspicious_records)
        
    except Exception as e:
        logger.error(f"❌ Batch 실행 중 에러 발생: {e}")
        sys.exit(1) # K8s Job 실패 처리를 위해 exit code 1 반환
        
    logger.info("✅ FDS Report Batch가 성공적으로 종료되었습니다.")
    sys.exit(0)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
