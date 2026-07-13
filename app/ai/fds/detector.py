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
        # TO-DO: 임계치(Threshold) 기반 룰 적용 로직 정교화
        return_cnt = record.get("return_count", 0)
        reason = record.get("reason", "")
        
        # 룰 1: 반품 횟수가 3회 이상이면서 사유가 '파손'인 경우 의심 유저로 분류
        if return_cnt >= 3 and reason == "파손":
            record["fraud_score"] = 90
            record["fraud_reason"] = "파손 사유 3회 이상 누적"
            suspicious_records.append(record)
            
    return suspicious_records
