import time
import json
from sqlmodel import Session, text
from app.core.database import engine
from langchain_core.messages import HumanMessage

def poll_and_process():
    """
    K8s HPA 환경에서도 데이터 유실(Data Loss) 없이 안전하게 작업을 가져오는
    PostgreSQL FOR UPDATE SKIP LOCKED 기반의 트랜잭션 큐 워커
    """
    while True:
        try:
            with Session(engine) as session:
                # 1. PENDING 상태인 작업을 하나만 안전하게(SKIP LOCKED) 가져옴
                # (Tech PM 장문경이 제공하는 인프라 락(Lock) 쿼리입니다. 수정하지 마세요.)
                sql = text("""
                    SELECT id, image_url FROM return_jobs 
                    WHERE status = 'PENDING' 
                    FOR UPDATE SKIP LOCKED 
                    LIMIT 1
                """)
                
                result = session.exec(sql).first()
                if result:
                    job_id = str(result.id)
                    image_url = result.image_url
                    print(f"[Worker] Picked up job {job_id}")
                    
                    # 2. 상태를 PROCESSING으로 변경하여 다른 워커 접근 차단
                    update_sql = text("UPDATE return_jobs SET status = 'PROCESSING' WHERE id = :job_id")
                    session.exec(update_sql, {"job_id": job_id})
                    session.commit()
                    
                    # -------------------------------------------------------------
                    # TODO: 3. LangGraph Supervisor (Star Topology) 호출 로직 구현
                    # -------------------------------------------------------------
                    # app.ai.supervisor.app_graph 를 import 한 뒤, 
                    # job_id를 thread_id로 삼아 invoke() 하세요.
                    # HITL 처리를 위해 graph.get_state(config).next 등을 활용해야 합니다.
                    print(f"[Worker] Running LangGraph Supervisor Pipeline for {job_id}... (Not Implemented)")
                    
                    # -------------------------------------------------------------
                    # TODO: 4. 결과 분석 및 DB 트랜잭션 종료 로직 구현
                    # -------------------------------------------------------------
                    # 그래프가 종료되면 반환된 ubci_score와 final_report를 추출하여 
                    # status='APPROVED' 또는 'WAITING_HUMAN'과 함께 UPDATE 하세요.
                    
                    # 더미 대기 (임시)
                    time.sleep(2)
                    
                else:
                    # 대기열에 작업이 없으면 2초 대기
                    time.sleep(2)
        except Exception as e:
            print(f"[Worker Error] {e}")
            time.sleep(5) # 에러 시 5초 대기 후 재시도

if __name__ == "__main__":
    print("[Worker] Starting PostgreSQL SKIP LOCKED Worker Daemon...")
    poll_and_process()
