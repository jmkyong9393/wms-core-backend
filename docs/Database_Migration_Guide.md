# Database Migration Guide

이 프로젝트는 Alembic으로 PostgreSQL 스키마 버전을 관리한다.

목표는 간단하다.

- DB가 변경되어도 기존 데이터는 유지한다.
- 시스템 실행 시 필요한 DB 변경을 자동으로 적용한다.
- DB를 업데이트하기 위해 Docker 볼륨을 삭제하지 않는다.

## 1. 일반 팀원이 알아야 할 내용

### 시스템 실행 및 업데이트

```bash
docker compose up -d --build
```

이 명령을 실행하면 다음 순서로 시스템이 시작된다.

```text
PostgreSQL 시작
→ Alembic이 DB를 최신 버전으로 업데이트
→ API와 worker 시작
```

새로운 컬럼이나 테이블이 추가되어도 PostgreSQL 볼륨과 기존 데이터 행은
유지된다.

`--build`는 최신 migration 파일을 Docker 이미지에 포함하기 위해 필요하다.

### 시스템 종료

```bash
docker compose down
```

이 명령은 컨테이너를 종료하지만 PostgreSQL 데이터는 삭제하지 않는다.

### 사용하면 안 되는 명령

```bash
docker compose down -v
```

`-v`를 붙이면 PostgreSQL 데이터 볼륨도 삭제된다. DB를 의도적으로 완전히
초기화하는 상황이 아니라면 사용하지 않는다.

일반 팀원은 Alembic 명령을 직접 실행할 필요가 없다.

## 2. DB 모델을 변경하는 개발자가 알아야 할 내용

SQLModel 모델에서 테이블, 컬럼, 인덱스 또는 제약조건을 변경한 개발자는
migration 파일을 함께 만들어야 한다.

### migration 생성

```bash
alembic revision --autogenerate -m "변경 내용"
```

예:

```bash
alembic revision --autogenerate -m "add user phone number"
```

생성된 `alembic/versions/*.py` 파일을 확인한 후 모델 변경과 함께 Git에
커밋한다.

자동 생성 결과에서는 다음 항목을 확인한다.

- 대상 테이블과 컬럼 이름이 맞는가?
- 컬럼 타입과 `nullable` 설정이 맞는가?
- 기존 데이터가 있는 테이블에 `NOT NULL` 컬럼을 안전하게 추가하는가?
- 의도하지 않은 테이블 또는 컬럼 삭제가 포함되지 않았는가?
- `upgrade()`와 `downgrade()`가 변경 의도에 맞는가?

모델만 변경하고 migration 파일을 만들지 않으면 다른 팀원의 DB에는 변경이
반영되지 않는다.

### 로컬 적용 및 확인

```bash
alembic upgrade head
alembic current
alembic check
```

- `upgrade head`: 아직 적용되지 않은 migration을 순서대로 적용한다.
- `current`: 현재 DB에 적용된 revision을 표시한다.
- `check`: 모델 변경에 대응하는 migration이 빠지지 않았는지 검사한다.

검증이 끝나면 다음 파일을 같은 커밋에 포함한다.

```text
app/models/...               모델 변경
alembic/versions/<revision>  DB migration
```

다른 팀원은 해당 코드를 받은 후 평소처럼 다음 명령만 실행하면 된다.

```bash
docker compose up -d --build
```

## 3. DB 관리자가 알아야 할 명령

### 현재 DB 버전 확인

```bash
alembic current
```

### 코드에 포함된 최신 버전 확인

```bash
alembic heads
```

정상적인 상태에서는 head가 하나만 표시되어야 한다.

### 최신 버전으로 업데이트

```bash
alembic upgrade head
```

Alembic은 DB의 현재 revision을 확인하고 아직 적용되지 않은 migration만
실행한다. 이미 적용한 migration은 다시 실행하지 않는다.

### 직전 버전으로 되돌리기

```bash
alembic downgrade -1
```

downgrade는 항상 안전한 작업이 아니다. 컬럼이나 테이블을 삭제하는 migration은
데이터를 잃을 수 있으므로 다음 순서를 따른다.

```text
1. DB 쓰기 작업 중단
2. DB 백업
3. 현재 revision 확인
4. downgrade 코드와 데이터 손실 가능성 검토
5. downgrade 실행
6. 이전 애플리케이션 버전 실행
7. 데이터와 기능 확인
```

baseline 이전으로 되돌리는 작업은 차단되어 있다.

```bash
alembic downgrade base
```

baseline 이전 상태가 필요하면 downgrade 대신 검증된 DB 백업을 복원한다.

### migration 누락 확인

```bash
alembic check
```

정상 결과:

```text
No new upgrade operations detected.
```

### migration 이력 확인

```bash
alembic history
```

## 4. 문제가 발생했을 때

새 버전 배포 후 문제가 발생했다고 해서 항상 DB부터 downgrade할 필요는 없다.

새 컬럼이나 인덱스가 이전 애플리케이션과 호환된다면 DB는 그대로 두고
애플리케이션만 이전 버전으로 되돌리는 방법이 더 안전하다. 이후 새로운
migration으로 문제를 수정한다.

```text
A → B(문제 발견) → C(B를 수정하는 새 migration)
```

한 번이라도 팀이나 운영 DB에 적용된 migration 파일은 수정하거나 삭제하지
않는다. 같은 revision이 환경마다 다른 작업을 의미하게 될 수 있기 때문이다.

Alembic은 migration의 적용 순서와 현재 DB 버전을 관리하지만, 삭제된 데이터를
자동으로 복구하지는 않는다. 데이터 복구는 DB 백업으로 수행한다.

## 5. 역할별 요약

### 일반 팀원

```bash
docker compose up -d --build
docker compose down
```

### DB 모델 변경 개발자

```bash
alembic revision --autogenerate -m "변경 내용"
alembic upgrade head
alembic check
```

모델 변경과 생성된 migration 파일을 함께 커밋한다.

### DB 관리자

```bash
alembic current
alembic heads
alembic history
alembic upgrade head
alembic downgrade -1
alembic check
```

DB 관리자는 migration 적용과 downgrade 전에 백업 및 데이터 손실 가능성을
확인한다.
