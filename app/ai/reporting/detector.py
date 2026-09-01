import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from sqlmodel import Session, text


def fetch_fds_policies(session: Session) -> Dict[str, float]:
    """
    DB(fds_policies 테이블)에서 실시간으로 룰 엔진 임계값 설정을 가져오는 함수
    - 관리자 API를 통해 값이 변경되면 배치 스크립트 실행 시 즉각 반영됩니다.
    """
    logger.info("DB에서 FDS 정책(Config) 임계값을 불러옵니다...")

    # 기본값 Fallback
    config = {
        "MAX_RETURN_30D": 3.0,
        "MIN_UBCI_SCORE": 30.0,
        "MAX_RETURN_90D": 5.0,
        "MAX_REFUND_AMT": 500000.0
    }

    try:
        result = session.execute(text("SELECT policy_key, policy_value FROM fds_policies"))
        for row in result:
            config[row[0]] = float(row[1])
    except Exception as e:
        logger.error(f"FDS 정책을 DB에서 불러오는 중 에러 발생, 기본값을 사용합니다: {e}")

    return config

def detect_black_consumers(raw_data: List[Dict[str, Any]], config: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    FDS 룰 엔진 기반 이상거래 탐지 함수 (Config-driven)
    - DB에서 불러온 동적 정책(config)을 적용하여 악성 유저를 적발합니다.
    """
    logger.info("FDS 룰 엔진 기반 이상거래 탐지를 시작합니다...")

    suspicious_records = []

    for record in raw_data:
        # DB 연동 규격에 맞춘 데이터 변수화 (시계열 지표 반영)
        customer_id = record.get("customer_id", "Unknown")
        customer_name = record.get("customer_name", "Unknown Name")
        returns_30d = record.get("returns_last_30d", 0)
        returns_90d = record.get("returns_last_90d", 0)
        avg_score = record.get("avg_ubci_score", 100.0)
        total_refund = record.get("total_refund_amount", 0)

        # 룰 1: 악성 블랙컨슈머 (최근 30일 반품 X회 이상 & 도서 상태 평균 Y점 이하)
        if returns_30d >= config.get("MAX_RETURN_30D", 3) and avg_score <= config.get("MIN_UBCI_SCORE", 30.0):
            record["fraud_score"] = 95
            record["fraud_reason"] = f"상습 고의 파손 의심 (최근 30일 반품 {returns_30d}회, 평균 UBCI {avg_score:.1f}점)"
            logger.warning(f"🚨 이상거래 탐지: {customer_name} ({customer_id}) - {record['fraud_reason']}")
            suspicious_records.append(record)

        # 룰 2: 요주의 고객 (최근 90일 반품이 잦거나 총 환불액이 임계치를 넘는 경우)
        elif returns_90d >= config.get("MAX_RETURN_90D", 5) or total_refund >= config.get("MAX_REFUND_AMT", 500000):
            record["fraud_score"] = 75
            record["fraud_reason"] = f"과도한 반품/환불 (총액: {total_refund}원, 최근 90일 횟수: {returns_90d}회)"
            logger.warning(f"🚨 이상거래 탐지: {customer_name} ({customer_id}) - {record['fraud_reason']}")
            suspicious_records.append(record)

    return suspicious_records
