import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

def detect_black_consumers(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    FDS 룰 엔진 기반 이상거래 탐지 함수
    - 최근 1주일 파손 반품 3회 이상 등 하드코딩된 Rule 적용
    """
    logger.info("FDS 룰 엔진 기반 이상거래 탐지를 시작합니다...")
    
    suspicious_records = []
    
    for record in raw_data:
        # DB 연동 규격에 맞춘 데이터 변수화
        user_id = record.get("user_id", "Unknown")
        return_cnt = record.get("return_count", 0)
        avg_score = record.get("avg_ubci_score", 100.0)
        total_refund = record.get("total_refund_amount", 0)
        reasons = record.get("return_reasons", [])
        
        # 룰 1: 악성 블랙컨슈머 (반품 3회 이상 & 도서 상태 평균 30점 이하) -> 고의 파손 의심
        if return_cnt >= 3 and avg_score <= 30.0:
            record["fraud_score"] = 95
            record["fraud_reason"] = f"상습 고의 파손 의심 (반품 {return_cnt}회, 평균 UBCI {avg_score:.1f}점)"
            suspicious_records.append(record)
            
        # 룰 2: 요주의 고객 (도서 상태와 무관하게 환불 금액이 너무 크거나, 잦은 단순변심)
        elif return_cnt >= 5 or total_refund >= 500000:
            record["fraud_score"] = 75
            record["fraud_reason"] = f"과도한 반품/환불 (총액: {total_refund}원, 횟수: {return_cnt}회)"
            suspicious_records.append(record)
            
    return suspicious_records
