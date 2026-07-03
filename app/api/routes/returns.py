from fastapi import APIRouter

router = APIRouter()

# TODO: 팀원 실습 과제
# 이 파일은 반품(Return) 관련 API 엔드포인트를 정의하는 라우터입니다.

@router.post("/upload")
def upload_return_image():
    """
    [과제 목표]
    사용자로부터 반품 이미지(또는 S3 URL)를 받아 Celery 워커에 작업을 비동기로 위임하는 엔드포인트를 구현하세요.
    
    [구현 지침]
    1. HTTP 상태 코드 202(Accepted)를 반환하도록 데코레이터를 설정하세요.
    2. DB 세션(Dependency Injection)을 활용하여 ReturnJob 테이블에 초기 상태('PENDING')를 기록하세요.
    3. worker.py에 정의된 `process_book_inspection` Task를 `apply_async`로 호출하여 큐에 밀어 넣으세요.
    4. 생성된 DB 레코드의 ID와 Celery Task ID를 JSON으로 반환하세요.
    """
    raise NotImplementedError("API 라우터 - /upload 엔드포인트를 구현하세요.")

@router.get("/{task_id}")
def get_return_status(task_id: str):
    """
    [과제 목표]
    작업 ID(task_id)를 통해 현재 검수 진행 상태를 조회하는 엔드포인트를 구현하세요.
    
    [구현 지침]
    1. Celery의 AsyncResult 객체를 통해 Redis에 기록된 워커의 실시간 상태를 조회하세요.
    2. 데이터베이스(Postgres)를 조회하여 현재 DB에 기록된 최종 처리 상태를 교차 검증하세요.
    """
    raise NotImplementedError("API 라우터 - 상태 조회 엔드포인트를 구현하세요.")
