# 🚀 WMS AI Platform: 팀원 로컬 개발 및 실행 가이드 (Local E2E Guide)

프론트엔드, 백엔드, AI 파트 개발자 여러분들이 로컬 환경에서 개발하고 테스트할 때 반드시 알아야 할 **통합 실행 가이드 및 협업 규칙**입니다.

---

## 1. 🐳 로컬 E2E 통합 실행 방법 (Docker Compose)

기존에는 프론트와 백엔드를 각각 따로 켜야 했지만, 이제 최상위(Root) 폴더에 통합된 `docker-compose.yml`이 구축되어 명령어 한 줄로 전체 인프라를 가동할 수 있습니다.

* **포함된 컨테이너 서비스:**
  * `wms-core-postgres` (DB — 호스트 **5433**)
  * `wms-core-redis` (Celery 브로커 — 호스트 **6380**)
  * `wms-core-chroma` (RAG 벡터 DB — 호스트 **8002**)
  * `wms-core-api` (FastAPI 백엔드 — 호스트 **8080**)
  * `wms-core-worker` / `wms-core-inspection-worker` / `wms-core-restock-worker` (Celery)
  * `wms-core-flower` (Celery 모니터링 — 호스트 **5556**)

> 포트는 개인 트랙(5432/6379/8001/8000)과 겹치지 않도록 전부 격리되어 있습니다.
> 두 트랙을 동시에 띄워도 충돌하지 않습니다.

### 📝 필수 초기 설정: `.env` 파일 생성
프로젝트 최상위 폴더에 `.env` 파일을 생성하고 아래 환경변수를 반드시 추가해야 합니다. (`.env` 파일은 보안상 Git에 공유되지 않으므로 팀원 각자가 로컬에 세팅해야 합니다.)

```env
OPENAI_API_KEY=여기에 OpenAI API Key 입력
CHROMA_SERVER_HOST=localhost
CHROMA_SERVER_PORT=8002

# 최초 MASTER 계정 (Settings 필수값 — 없으면 앱과 테스트가 기동되지 않습니다)
INITIAL_MASTER_EMPLOYEE_ID=NZ0000000
INITIAL_MASTER_NAME=관리자이름
INITIAL_MASTER_PASSWORD=여기에 초기 비밀번호

# S3/CloudFront (검수 이미지 업로드) — 프론트 .env.local에도 동일 값이 필요합니다
OSS_REGION=ap-northeast-2
OSS_BUCKET_NAME=버킷명
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
CLOUDFRONT_DOMAIN=https://xxxx.cloudfront.net

# 배포 프론트 오리진 (로컬은 비워둡니다. 쉼표로 여러 개 지정)
CORS_ALLOWED_ORIGINS=
```

> ⚠️ `CHROMA_SERVER_PORT`는 팀 트랙 격리 포트인 **8002**입니다(개인 트랙 8001과 다름).
> 프론트는 `.env.local`에 `NEXT_PUBLIC_API_URL=http://localhost:8080` 과 위 `OSS_*`/
> `CLOUDFRONT_DOMAIN` 을 함께 넣어야 검수 이미지 업로드가 동작합니다.

이 변수들이 설정되어야 로컬 파이썬 스크립트(`ingest.py`, `test_search.py` 등)가 백그라운드에 떠있는 도커 컨테이너(ChromaDB)와 정상적으로 통신할 수 있습니다.

### 📌 실행 명령어
터미널을 열고 **프로젝트 최상위 폴더(예: `wms-ai-platform/` 등 본인이 Clone 받은 루트 폴더)** 로 이동한 뒤 아래 명령어를 실행하세요.

```bash
# 전체 시스템 백그라운드 구동 (최초 실행 시 빌드 진행)
docker-compose up -d --build

# 실시간 전체 로그 확인 (에러 디버깅용)
docker-compose logs -f

# 특정 컨테이너(예: api)의 로그만 확인하고 싶을 때
docker-compose logs -f api

# 전체 시스템 종료 및 컨테이너 삭제
docker-compose down
```

> **🔥 주의사항:** API 서버(`wms-api`)와 워커(`wms-worker`)는 **DB와 Redis가 완전히 가동(Healthy)된 이후에 자동으로 부팅**되도록 설정되어 있습니다. 처음 켰을 때 API가 바로 뜨지 않는다고 당황하지 마시고 로그를 지켜보세요.

---

## 2. 🧱 각 파트별 개발 및 테스트 규칙

### 🌐 프론트엔드 파트
- 로컬 웹 접속 주소: `http://localhost:3001` (`npm run dev`)
- 백엔드 API 서버는 `http://localhost:8080`에 떠있습니다. API 요청은 반드시 `NEXT_PUBLIC_API_URL` 환경변수를 통해 호출하세요.
- 배포 도메인을 추가할 때는 백엔드 `.env`의 `CORS_ALLOWED_ORIGINS`에 오리진을 넣습니다(코드 수정 불필요).

### ⚙️ 백엔드 파트 (API & DB)
- 로컬 API Docs (Swagger): `http://localhost:8080/docs`
- DB 접속: `localhost:5433` (User: `admin`, PW: `password`, DB: `wms_db`)
- **Celery 비동기 테스트:** `/upload` API를 통해 반품 작업을 요청하면 즉시 응답(202)이 오고, 백그라운드의 Redis 큐를 통해 `wms-worker`가 작업을 처리합니다.

### 🧠 AI 워커 파트 (LangGraph)
- AI 검수 로직 수정 및 테스트는 백엔드 폴더 내부의 `app/ai/` 하위에서 진행합니다.
- 코드를 변경한 뒤에는 워커가 변경사항을 물고 다시 뜰 수 있도록 `docker-compose restart worker`를 실행해 주세요.

### 🧩 AI 검수 필수 사전 준비: YOLO 모델 가중치 배치
YOLO 가중치(`*.pt`)는 용량 문제로 Git에 올라가지 않습니다 (`.gitignore`의 `models/*.pt`).
클론 직후에는 `models/` 폴더가 비어 있어 **AI 검수 요청 시 FileNotFoundError로 실패**하므로,
공유 드라이브(models_registry)에서 아래 4개 파일을 받아 백엔드 루트의 `models/` 폴더에 넣어야 합니다.

| 파일명 | 역할 |
| --- | --- |
| `general_binary_team_s3_v2_best.pt` | 범용 결함 탐지 (재현율 담당) |
| `doodle_best.pt` | 낙서/손글씨 전담 |
| `yolov8_high_precision_base.pt` | 오탐 억제용 정밀 모델 (현재 미사용, Phase 5 앙상블 예정분) |
| `yolov8x-worldv2.pt` | 책 영역 탐지 (YOLO-World) |

> `YOLO_MODEL_SPECS`에 정의된 `physical4_best.pt`는 **학습본이 아직 없습니다.**
> `YOLO_ENABLED_MODELS`에 physical4를 추가하면 FileNotFoundError가 나는 것이 정상이며,
> 클래스가 다른 다른 가중치를 그 이름으로 넣지 마세요(탐지 결과가 조용히 왜곡됩니다).

### 📚 AI RAG 파이프라인 (ChromaDB) 시딩·테스트

⚠️ **정식 시딩 절차가 변경되었습니다.** Policy Agent가 실제로 조회하는 컬렉션은
`wms_ubci_policies`이며, 이를 채우는 명령은 아래 한 줄입니다. (`ingest.py`는 Policy Agent가
읽지 않는 legacy 컬렉션을 적재하므로 실행해도 RAG가 붙지 않습니다.)

1. **정책 데이터 적재 (정식 시딩)** — UBCI 명세서 + WMS 표준 운영 정책서를 청크 분할 적재
   ```bash
   uv run python -m app.ai.rag.policy_search
   ```
   Docker만 쓰는 경우에는 컨테이너로 동일 작업을 실행할 수 있습니다:
   ```bash
   docker compose run --rm rag-seed
   ```

2. **유사도 검색 테스트 (Retrieval)**
   정식 컬렉션(`wms_ubci_policies`) 검색 품질은 Policy Agent 실행 로그(`policy_rag_status=USED`)로
   확인합니다. `test_search.py`는 legacy 컬렉션(`wms_return_policies`) 전용 실험 도구이므로
   `ingest.py`로 적재한 데이터에만 사용하세요.

### 📊 대시보드용 주간 분석 배치 (Reporting) 테스트
AI 모듈에서 산출된 데이터를 바탕으로 재무/품질/포캐스팅 지표를 연산하는 주간 리포트 배치 스크립트를 수동으로 실행해 볼 수 있습니다. 실제 배포 환경에서는 K8s CronJob에 의해 스케줄링되지만, 로컬 개발 단계에서는 아래 명령어를 통해 직접 실행하고 터미널에서 산출된 JSON 결과를 확인합니다.

```bash
uv run python -m app.ai.reporting.report_batch
```

---

## 3. 🚨 Git 협업 및 배포 (CI/CD) 규칙

저희 프로젝트는 단기 속성(6+1주)으로 진행되므로 배포 파이프라인(GitHub Actions -> AWS EKS)이 이미 자동화되어 있습니다. **따라서 다음의 Git 룰을 엄격하게 지켜주세요.**

1. **절대 `main` 브랜치에 직접 Push하지 마세요.**
   - 모든 작업은 Kanban 티켓 번호 기반의 브랜치 명명 규칙을 따릅니다.
   - 기능 개발 시 `feat/<이슈번호>-<작업내용>` (예: `feat/BE-1.2-s3-upload`) 브랜치를 생성하여 작업하세요.
   - 버그 수정은 `fix/...`, UI 작업은 `design/...` 접두사를 사용합니다.
2. **배포는 오직 PR(Pull Request) 병합 시에만 일어납니다.**
   - 기능이 완성되면 `main` 브랜치를 향해 PR을 올리고, **Tech PM(장문경 님)의 코드 리뷰와 승인(Merge)** 을 받아야만 클라우드(AWS EKS) 서버에 배포됩니다.
3. **오류가 나면 즉시 공유하세요.**
   - Github Actions가 실패하거나 Docker 빌드 에러가 나면 지체 없이 슬랙/단톡방에 로그 캡처본을 올려주세요.

---

## 4. ✅ 품질 게이트 (PR 전에 로컬에서 통과시키기)

CI(`pr-check.yml`)가 아래를 **차단 게이트**로 검사합니다. 로컬에서 먼저 돌려 보세요.

### 백엔드
```bash
uv run ruff check app/                          # 규칙 위반 0건이어야 통과
uv run ruff format --check app/
uv run pytest -q                                # 전체 통과 필요
```
- 규칙셋과 ignore 사유는 `pyproject.toml`의 `[tool.ruff.lint]`에 적혀 있습니다.
  규칙을 되살리거나 새로 무시할 때는 **사유를 주석으로 남기세요.**
- mypy는 아직 비차단입니다. 타입 부채 정리 후 차단으로 승격할 예정입니다.

### 프론트엔드
```bash
npm run lint        # error 0건이어야 통과 (warning은 허용)
npm run test:run    # vitest 전체 통과 필요
npm run build
```
- 기존 타입 부채(`no-explicit-any` 17건)와 React Compiler 경고 12건은
  `eslint.config.mjs`에서 **warn으로 낮춰 가시화**해 두었습니다.
  새로 `any`를 늘리지 말고, 여력이 되면 줄여 주세요.

행운을 빕니다! 🍀
