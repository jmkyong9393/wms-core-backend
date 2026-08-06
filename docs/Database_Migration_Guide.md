# Database Migration Guide

이 문서는 PostgreSQL 데이터를 유지하면서 Alembic으로 DB 스키마를
버전 관리하기 위한 팀 표준 절차를 정의한다.

## 1. 기본 원칙

- SQLModel 모델과 Alembic migration 파일은 같은 변경 단위로 관리한다.
- 공유되거나 배포된 migration 파일은 수정하거나 삭제하지 않는다.
- `docker compose down -v`는 DB를 의도적으로 폐기할 때만 사용한다.
- migration 적용 전에는 DB를 백업하고 쓰기 트래픽을 중단한다.
- 자동 생성된 migration은 실행 전에 SQL, 데이터 보정, downgrade를 검토한다.
- 운영 DB에서 `alembic downgrade base`를 실행하지 않는다. baseline의
  downgrade는 모든 업무 테이블을 제거한다.

현재 baseline revision은 다음과 같다.

```text
2da43d0a4454 (baseline schema)
```

이 revision은 Alembic 도입 시점의 SQLModel metadata 전체를 나타낸다.
빈 DB에서는 `alembic upgrade head`가 baseline부터 현재 head까지 순서대로
스키마를 생성한다.

## 2. 기존 DB 최초 편입

기존 DB에는 이미 업무 테이블이 있으므로 baseline migration을 직접 실행하면
안 된다. 또한 실제 스키마를 확인하지 않고 baseline을 `stamp`해서도 안 된다.

### 2.1 쓰기 중단 및 백업

API, worker, batch처럼 DB에 쓰는 프로세스를 먼저 중단한다. 그다음 백업한다.

```bash
docker exec wms-core-postgres pg_dump \
  -U admin \
  -d wms_db \
  -Fc \
  -f /tmp/wms-before-alembic.dump

docker cp \
  wms-core-postgres:/tmp/wms-before-alembic.dump \
  ./wms-before-alembic.dump
```

백업 파일은 DB 비밀정보와 업무 데이터를 포함할 수 있으므로 Git에 추가하지
않는다.

### 2.2 현재 확인된 legacy 스키마 보정

Alembic 도입 당시 로컬 DB에는 아래 두 컬럼이 없고, 나머지 schema는 현재
SQLModel metadata와 일치하는 것으로 확인됐다.

```text
return_jobs.ai_inspection_started_at
return_jobs.ai_inspection_completed_at
```

기존 행을 보존하면서 다음 순서로 보정한다.

```sql
ALTER TABLE return_jobs
    ADD COLUMN IF NOT EXISTS ai_inspection_started_at TIMESTAMP;

UPDATE return_jobs
SET ai_inspection_started_at = COALESCE(created_at, CURRENT_TIMESTAMP)
WHERE ai_inspection_started_at IS NULL;

ALTER TABLE return_jobs
    ALTER COLUMN ai_inspection_started_at SET NOT NULL;

ALTER TABLE return_jobs
    ADD COLUMN IF NOT EXISTS ai_inspection_completed_at TIMESTAMP;
```

환경마다 DB 상태가 다를 수 있으므로, 두 컬럼만 보정하면 모든 환경이 같을
것이라고 가정해서는 안 된다.

### 2.3 baseline 등록 및 일치 검사

실제 스키마를 보정한 후 baseline을 실행하지 않고 현재 version으로 등록한다.

```bash
alembic stamp 2da43d0a4454
alembic current
alembic check
```

정상 결과는 다음 조건을 모두 만족해야 한다.

- `alembic current`가 `2da43d0a4454 (head)`를 표시한다.
- `alembic check`가 `No new upgrade operations detected.`를 표시한다.

`alembic check`에서 차이가 발견되면 애플리케이션을 시작하지 않는다. 먼저
version 표시를 제거하고 실제 차이를 검토한다.

```bash
alembic stamp base
```

차이를 안전하게 보정한 후 baseline 등록과 검사를 다시 수행한다.

## 3. 신규 DB 초기화

데이터가 없는 신규 DB는 baseline을 포함한 모든 migration을 실행한다.

```bash
alembic upgrade head
alembic current
alembic check
```

Docker Compose에서는 `migrate` 서비스가 먼저 완료된 후 API와 worker가
시작한다. migration 파일은 이미지에 포함되므로 코드 변경 후 반드시 이미지를
다시 빌드한다.

```bash
docker compose up -d --build
```

## 4. 스키마 변경 작업

### 4.1 migration 생성

모델을 변경한 뒤 최신 revision이 적용된 개발 DB에서 생성한다.

```bash
alembic current
alembic revision --autogenerate -m "describe schema change"
```

생성 파일에서 다음 내용을 반드시 검토한다.

- 컬럼명과 테이블명
- `nullable`, 타입, 길이와 precision
- 기존 행을 위한 backfill
- 외래키, unique, check constraint와 index
- PostgreSQL enum 생성·변경·삭제
- downgrade 시 데이터 손실 여부

모델 변경만 커밋하고 migration 파일을 누락해서는 안 된다.

### 4.2 로컬 검증

중요한 migration은 운영 데이터의 구조를 복제한 별도 DB에서 검증한다.

```bash
alembic upgrade head
alembic check
alembic downgrade -1
alembic upgrade head
alembic check
```

`DROP COLUMN`, 컬럼 타입 축소, enum 값 삭제, 대량 backfill은 데이터 손실이나
장시간 lock을 일으킬 수 있으므로 별도 승인과 복구 검증이 필요하다.

## 5. 배포 절차

보수적인 배포 순서는 다음과 같다.

```text
1. 적용 대상 revision과 단일 head 확인
2. DB 백업 및 복원 가능 여부 확인
3. API·worker·batch 쓰기 중단
4. migration 실행
5. current 및 schema 상태 확인
6. 새 애플리케이션 시작
7. smoke test
```

확인 명령은 다음과 같다.

```bash
alembic heads
alembic current
alembic upgrade head
alembic current
alembic check
```

`alembic heads` 결과가 둘 이상이면 migration branch가 갈라진 것이다. 임의로
하나를 삭제하지 말고 merge revision을 작성해 단일 head로 합친다.

## 6. 장애와 rollback

### 6.1 이전 코드와 호환되는 DB 변경

컬럼이나 index 추가처럼 이전 코드가 새 스키마에서도 동작한다면 DB revision은
유지하고 애플리케이션만 이전 버전으로 되돌리는 방법을 우선한다. 이후 새
migration으로 문제를 보정한다.

```text
A → B(문제 발견) → C(B를 보정)
```

이미 공유되거나 적용된 `B` 파일을 수정해서는 안 된다. 동일한 revision ID가
환경마다 다른 SQL을 의미하게 되기 때문이다.

### 6.2 DB도 되돌려야 하는 변경

이전 코드가 변경된 DB에서 동작하지 않을 때만 downgrade를 검토한다.

```text
1. 쓰기 트래픽 중단
2. DB 백업
3. alembic current 확인
4. downgrade SQL과 데이터 손실 검토
5. alembic downgrade <previous_revision>
6. 이전 애플리케이션 배포
7. 데이터 정합성과 기능 검증
```

직전 revision을 되돌리는 명령은 다음과 같다.

```bash
alembic downgrade -1
```

Alembic은 어떤 migration을 어떤 순서로 되돌릴지 관리하지만, 삭제된 데이터까지
복구하지 않는다. 데이터 복구는 사전에 검증한 DB 백업으로 수행한다.

### 6.3 migration 실행 자체가 실패한 경우

PostgreSQL의 일반적인 transactional DDL에서는 실패한 migration이 rollback되고
`alembic_version`도 이전 revision에 머문다. 단, transaction 밖에서 수행되는
DDL이나 별도 commit을 포함한 migration은 동일하게 보장되지 않을 수 있다.

실패 후 다음을 확인한다.

```bash
alembic current
alembic heads
```

DB에 성공 적용된 적 없는 migration은 수정할 수 있다. 한 환경이라도 성공
적용됐다면 기존 파일을 고치지 않고 후속 보정 migration을 작성한다.

## 7. 금지 사항

다음 작업은 명시적인 DB 폐기 또는 복구 승인이 없으면 수행하지 않는다.

```bash
docker compose down -v
alembic downgrade base
```

다음 행동도 금지한다.

- 적용된 migration 파일의 revision ID 또는 `down_revision` 변경
- 적용된 migration 파일 삭제 및 순서 재작성
- schema 일치 확인 없는 `alembic stamp`
- 백업 없는 파괴적 migration 실행
- 여러 개발자가 만든 migration head 중 하나를 임의 삭제
