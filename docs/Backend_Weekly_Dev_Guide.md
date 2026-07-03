# 📅 주차별 백엔드 실무 개발 가이드 (Backend Weekly Dev Guide)

본 문서는 WMS AI Platform 백엔드 팀원들을 위한 주차별 개발 가이드입니다.
프로젝트 소스 코드(`app/worker.py`, `app/api/routes/returns.py` 등)는 뼈대만 남아 있습니다. 
아래 가이드를 참고하여 여러분이 직접 인프라와 로직을 설계하고 채워 넣어 보세요!

---

## 🚀 [Week 2] 비동기 처리(Celery+Redis) 및 AI 워커 연동

이번 주차의 목표는 **사용자가 업로드한 반품 이미지를 메인 API 서버가 막히지 않도록 비동기 큐(Queue)에 넘기고, 백그라운드 워커가 이를 꺼내어 AI(LangGraph)로 검수하는 파이프라인**을 구축하는 것입니다.

### 📝 Mission 1: API 라우터 구현 (`app/api/routes/returns.py`)

FastAPI의 엔드포인트를 구현하여 사용자 요청을 받아들여야 합니다.

**구현 목표:**
1. **`/upload` 엔드포인트 (POST):**
   - HTTP 상태 코드 `202 (Accepted)`를 반환하도록 설계하세요.
   - DB 세션을 사용하여 `ReturnJob` 테이블에 초기 상태(`PENDING`)를 삽입하세요.
   - `worker.py`에 만들어둔 `process_book_inspection` Task를 `apply_async()` 메서드로 호출하여 워커에 비동기 위임하세요.
   - 생성된 DB 레코드의 ID와 Celery Task ID를 반환하세요.

2. **`/{task_id}` 엔드포인트 (GET):**
   - 사용자가 자신의 검수 작업이 끝났는지 확인하는 조회 API입니다.
   - Celery의 `AsyncResult`를 사용해 Redis에 저장된 실시간 상태(`PENDING`, `SUCCESS` 등)를 가져오세요.
   - DB 테이블도 함께 조회하여 상태를 크로스체크하여 리턴해 보세요.

---

### 📝 Mission 2: Celery 기반 백그라운드 워커 구현 (`app/worker.py`)

API 서버가 던진 Task를 묵묵히 처리하는 일꾼(Worker)을 세팅합니다.

**구현 목표:**
1. **Celery 인스턴스 초기화:**
   - 환경 변수 `CELERY_BROKER_URL`을 통해 Redis에 연결되도록 Celery App 객체를 생성하세요.
   - `worker_concurrency`, `task_time_limit` 등의 설정을 통해 AI 연산 중 발생할 수 있는 OOM(Out of Memory)이나 무한 루프를 방지하세요.

2. **`process_book_inspection` 함수 로직 (Task):**
   - Task가 시작되면 즉시 DB의 해당 `ReturnJob` 상태를 `PROCESSING`으로 업데이트하세요.
   - (핵심) `app.ai.supervisor`에 정의된 `app_graph`를 import한 뒤, `.invoke()`를 호출하여 도서 검수 AI 파이프라인을 실행하세요.
   - AI 파이프라인이 반환한 `ubci_score` (상대적 비율 BBox 점수)를 가져오세요.
   - 점수에 따라 Dynamic Pricing(동적 가격 책정) 알고리즘을 적용하여 기존 도서 가격 대비 환불 예상액을 계산해 보세요.
   - 모든 검수가 끝나면 `ReturnJob`의 상태를 `APPROVED` 또는 `WAITING_HUMAN`으로 변경하고 DB 트랜잭션을 Commit(`session.commit()`) 하세요.
   - 네트워크 오류 등으로 AI 호출이 실패할 경우를 대비해 `try-except` 블록과 `self.retry()` 로직을 반드시 추가하세요.

---

> **💡 Tech PM의 팁 (Mitigation):**
> "코드를 짜다가 막히면 공식 문서(Celery, FastAPI, SQLModel)를 먼저 찾아보는 습관을 들이세요! 로컬 환경에서 테스트할 때는 제공해 드린 최상위 `docker-compose.yml`을 띄워서 Redis와 Postgres가 정상 구동 중인지 꼭 확인해야 합니다."
