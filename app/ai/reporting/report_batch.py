import logging
from typing import List, Dict, Any
from sqlmodel import Session, text
import uuid
from app.core.database import engine
from app.models.wms import (
    FdsReport,
    NotificationCategory,
    NotificationSeverity,
    WeeklyInsight,
)
from app.services.notification_service import (
    create_notification_for_tenant,
)
from app.services.redis_pubsub import (
    publish_tenant_notification_event,
)

logger = logging.getLogger(__name__)

def fetch_recent_returns(
    session: Session,
) -> List[Dict[str, Any]]:
    logger.info("DB에서 최근 반품 데이터를 조회합니다..")

    query = text("""
    SELECT
        r.tenant_id,
        o.customer_id,
        o.customer_name,
        COUNT(
            CASE
                WHEN r.created_at >= NOW() - INTERVAL '30 days'
                THEN 1
            END
        ) AS returns_last_30d,
        COUNT(
            CASE
                WHEN r.created_at >= NOW() - INTERVAL '90 days'
                THEN 1
            END
        ) AS returns_last_90d,
        COUNT(
            CASE
                WHEN r.created_at >= NOW() - INTERVAL '7 days'
                THEN 1
            END
        ) AS weekly_return_count,
        AVG(r.ubci_score) AS avg_ubci_score,
        SUM(oi.final_price) AS total_refund_amount,
        STRING_AGG(r.final_report, ', ') AS final_report
    FROM return_jobs r
    JOIN orders o ON r.order_id = o.id
    JOIN order_items oi
        ON r.order_id = oi.order_id
        AND r.book_id = oi.book_id
    WHERE r.created_at >= NOW() - INTERVAL '90 days'
      AND r.status = 'APPROVED'
    GROUP BY r.tenant_id, o.customer_id, o.customer_name;
    """)

    result = session.execute(query)
    data = []

    for row in result:
        data.append(
            {
                "tenant_id": str(row[0]),
                "customer_id": str(row[1]),
                "customer_name": row[2],
                "returns_last_30d": row[3] or 0,
                "returns_last_90d": row[4] or 0,
                "weekly_return_count": row[5] or 0,
                "avg_ubci_score": (
                    float(row[6])
                    if row[6] is not None
                    else 100.0
                ),
                "total_refund_amount": (
                    float(row[7])
                    if row[7] is not None
                    else 0.0
                ),
                "final_report": row[8] or "",
            }
        )

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
        SELECT COUNT(*) FROM rejected_items WHERE status = 'REJECT_HOLD';
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
    WHERE r.created_at >= NOW() - INTERVAL '7 days' 
      AND (r.status = 'REJECTED' OR r.condition_grade = 'REJECT')
    GROUP BY b.publisher
    ORDER BY count DESC LIMIT 2;
    """)
    result = session.execute(query)
    return {row[0]: row[1] for row in result}

def save_fds_report(
    session: Session,
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    logger.info(
        f"총 {len(results)}건의 이상거래 의심 내역을 보고합니다."
    )

    # SSE 알림 발행 대상을 구분하기 위한 반환값.
    # 기존 FDS 보고서 저장·갱신 정책은 변경하지 않는다.
    alert_candidates = []

    for res in results:
        tenant_id = uuid.UUID(res["tenant_id"])
        cid = uuid.UUID(res["customer_id"])
        new_score = res["fraud_score"]
        new_reason = res["fraud_reason"]

        # 1. 기존 의심 이력 조회
        existing = session.execute(
            text("""
                SELECT id, fraud_score
                FROM fds_reports
                WHERE tenant_id = :tenant_id
                  AND customer_id = :cid
            """),
            {
                "tenant_id": tenant_id,
                "cid": cid,
            },
        ).fetchone()

        if not existing:
            # 최초 적발: 신규 INSERT
            logger.warning(
                f"신규 이상거래 의심 유저: {res}"
            )
            report = FdsReport(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                customer_id=cid,
                fraud_score=new_score,
                fraud_reason=new_reason,
            )
            session.add(report)

            # 신규 적발 건을 SSE 알림 후보에 추가
            alert_candidates.append(res)

        else:
            old_id = existing[0]
            old_score = existing[1]

            if new_score > old_score:
                # 2. 이미 적발되었으나 위험도가 더 상승한 경우:
                # UPDATE 및 detected_at 갱신
                logger.warning(
                    "이상거래 위험도 상향 갱신 "
                    f"(점수: {old_score} -> {new_score}): {res}"
                )
                session.execute(
                    text("""
                        UPDATE fds_reports
                        SET fraud_score = :score,
                            fraud_reason = :reason,
                            detected_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                    """),
                    {
                        "score": new_score,
                        "reason": new_reason,
                        "id": old_id,
                    },
                )

                # 위험도 상승 건을 SSE 알림 후보에 추가
                alert_candidates.append(res)

            else:
                # 3. 동일 점수거나 점수가 더 낮아진 경우 무시
                # (최초 적발의 detected_at 보존)
                logger.info(
                    "기존 의심 유저 유지 "
                    f"(점수 악화 없음) - {cid}"
                )

    return alert_candidates

def generate_weekly_insights(
    session: Session,
    raw_return_data: List[Dict[str, Any]], 
    inventory_stats: Dict[str, Any], 
    order_stats: Dict[str, Any]
) -> Dict[str, Any]:
    logger.info("대시보드용 주간 인사이트 통합 분석을 시작합니다 (SQL Push-down)...")
    
    if not raw_return_data:
        # FDS 데이터가 없더라도 인건비 절감액은 계산될 수 있도록 빈 딕셔너리 반환하지 않음 (이전 로직 수정)
        logger.info("최근 반품 데이터(raw_return_data)가 없지만 전체 검수 건수 집계는 계속 진행합니다.")
        
    # 반품 성공/실패, 중고 입고 성공/실패 여부와 관계없이(단, 사람이 개입한 HITL 제외) 최근 7일 내 AI가 온전히 검수를 마친 건만 조회
    total_ai_inspections = session.execute(
        text("SELECT COUNT(*) FROM return_jobs WHERE created_at >= NOW() - INTERVAL '7 days' AND status IN ('APPROVED', 'REJECTED')")
    ).scalar() or 0
    
    saved_labor_cost_krw = int(total_ai_inspections * 90 * 9860 / 3600)
    
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
            alert_candidates = save_fds_report(
                session,
                suspicious_records,
            )

            notification_events = []

            for record in alert_candidates:
                fraud_score = record["fraud_score"]
                severity = (
                    NotificationSeverity.HIGH
                    if fraud_score >= 90
                    else NotificationSeverity.MEDIUM
                )

                notification = create_notification_for_tenant(
                    session=session,
                    tenant_id=uuid.UUID(record["tenant_id"]),
                    category=NotificationCategory.FDS_ALERT,
                    severity=severity,
                    title="반품·환불 이상거래 탐지",
                    message=(
                        f"{record['customer_name']} 고객의 이상거래가 탐지되었습니다. "
                        f"{record['fraud_reason']}"
                    ),
                    payload={
                        "customer_id": record["customer_id"],
                        "fraud_score": fraud_score,
                    },
                )

                notification_events.append(
                    {
                        "tenant_id": str(notification.tenant_id),
                        "event": {
                            "id": str(notification.id),
                            "category": notification.category.value,
                            "severity": notification.severity.value,
                            "title": notification.title,
                            "message": notification.message,
                            "timestamp": notification.created_at.isoformat(),
                            "read": False,
                        },
                    }
                )

            save_insight_report(session, insights)

            session.commit()

            # DB 커밋이 성공한 알림만 실시간으로 발행한다.
            for notification_event in notification_events:
                try:
                    publish_tenant_notification_event(
                        tenant_id=notification_event["tenant_id"],
                        event=notification_event["event"],
                    )
                except Exception:
                    logger.exception(
                        "FDS 알림 Redis 발행에 실패했습니다. "
                        "notification_id=%s",
                        notification_event["event"]["id"],
                    )
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
