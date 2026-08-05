# WMS AI 플랫폼 배치 작업(CronJob) 로컬 테스트 가이드

이 문서는 로컬 개발 환경(Docker + Kind)에서 K8s CronJob으로 설정된 파이썬 배치 스크립트(`auto_po_batch`, `report_batch`)를 테스트하는 방법을 안내합니다.

---

## 1. ⚙️ 사전 필수 도구 설치 (Kind 설정)

로컬에서 가상 Kubernetes(K8s) 환경을 구동하기 위해 **Kind (Kubernetes in Docker)** 도구가 필요합니다. 아래 방법 중 하나를 선택해 설치합니다.

### 방법 A: 패키지 매니저로 간편 설치 (권장)
PowerShell 터미널에서 패키지 매니저를 통해 전역(Global)에 설치합니다. 시스템 경로(PATH)가 자동으로 설정되어 레포지토리 폴더 안에 실행 파일이 없어도 즉시 동작합니다.
```powershell
# Windows 기본 제공 Winget으로 설치
winget install Kubernetes.kind

# 또는 Chocolatey로 설치
choco install kind
```
* **주의:** 설치 완료 후, 환경 변수 반영을 위해 현재 열려 있는 에디터(VS Code 등)와 터미널 창을 **완전히 재시작**해야 `kind` 명령어가 인식됩니다.

### 방법 B: 직접 다운로드하여 프로젝트 폴더에 배치
인터넷 방화벽 등으로 패키지 매니저 사용이 어려운 경우 직접 다운로드하여 프로젝트 루트에 위치시킵니다.
1. [Kind 공식 GitHub Releases](https://github.com/kubernetes-sigs/kind/releases)에서 `kind-windows-amd64` 파일을 다운로드합니다.
2. 파일 이름을 `kind-windows-amd64.exe`로 변경한 후, 백엔드 레포지토리 루트(`wms-core-backend/`) 폴더에 저장합니다.
3. 이 경우, 모든 명령어 입력 시 `kind` 대신 **`.\kind-windows-amd64.exe`**로 대체하여 입력해야 합니다.

---

## 2. 🔐 Kubernetes Secret 설정 (환경 변수 파일 생성)

배치 작업들은 K8s 컨테이너 내부에서 격리되어 실행되므로, DB 접속 주소 및 비밀번호 정보를 담은 시크릿 파일들이 로컬 클러스터에 주입되어야 합니다. 아래 양식에 맞추어 **로컬에 직접 YAML 파일을 생성**해 주세요. (보안상 Git 관리 대상에서 제외되어 있습니다.)

### 📁 1. `k8s/wms-master-secret.yaml` 생성
최초 부팅 시 DB에 자동 인서트될 최고 관리자 계정 정보를 담고 있습니다.

* **생성 경로:** `wms-core-backend/k8s/wms-master-secret.yaml`
* **파일 내용:**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: wms-master-secret
  namespace: default
type: Opaque
stringData:
  # 로컬 개발 환경(.env)에 정의된 계정 정보와 완벽히 동기화해 줍니다.
  INITIAL_MASTER_EMPLOYEE_ID: "NZ0000000"
  INITIAL_MASTER_NAME: "initial_master"
  INITIAL_MASTER_PASSWORD: "Newzed1234!"
  AUTO_PO_TENANT_ID: "33c9450c-cf03-4327-ab52-fd6d14ea0dc8"
```

### 📁 2. `k8s/fds-secret.yaml` 생성
K8s 환경 내에서 호스트의 PostgreSQL DB에 접근하기 위한 자격 증명입니다.

* **생성 경로:** `wms-core-backend/k8s/fds-secret.yaml`
* **파일 내용:**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: fds-db-secret
  namespace: default
type: Opaque
stringData:
  # 로컬 호스트(PC) Docker DB 포트 5433를 바라보는 커넥션 스트링
  DATABASE_URL: "postgresql://admin:password@host.docker.internal:5433/wms_db"
```

---

## 3. 📦 도커 빌드 및 K8s 이미지 주입

배치 소스코드(`.py`)나 라이브러리 의존성이 변경된 경우, 최신 코드를 가상 K8s 노드로 주입해 주는 과정이 필수적입니다.

```powershell
# 1. 백엔드 최신 소스코드 도커 이미지 빌드
docker build -t wms-ai:latest .

# 2. 빌드된 이미지를 K8s(Kind) 클러스터 내부로 전송
kind load docker-image wms-ai:latest --name wms-cluster
```
*(수동 다운로드 방식을 쓰는 경우 `.\kind-windows-amd64.exe load ...` 로 입력)*

---

## 4. ⚡ FDS (이상 탐지 및 핫스팟) 배치 테스트 (원클릭 자동화)

FDS 및 대시보드 통계 배치(`report_batch.py`)는 매번 DB 트랜잭션을 비우고, 시나리오 데이터를 주입하고, 수동으로 K8s Job을 삭제/재생성하는 일련의 번거로운 작업들이 필요합니다. 

이 모든 과정을 하나로 묶은 **자동화 테스트 스크립트**를 활용해 쉽고 빠르게 검증을 마칠 수 있습니다.

### 🚀 원클릭 통합 자동화 테스트 실행 방법
백엔드 터미널(`wms-core-backend`) 환경에서 아래 명령어를 실행합니다.
```powershell
# K8s 시크릿 배포 ➡️ DB 데이터 초기화 ➡️ 시나리오 시딩 ➡️ K8s 수동 잡 생성 및 실시간 로그 스트리밍 수행
powershell -ExecutionPolicy Bypass -File .\scripts\run_fds_batch_test.ps1
```

<details>
<summary>🔍 (참고용) 스크립트 내부에서 수행하는 수동 명령어 일람</summary>

```powershell
# 1. K8s 시크릿 반영
kubectl apply -f k8s/wms-master-secret.yaml
kubectl apply -f k8s/fds-secret.yaml

# 2. 기존 로컬 DB 초기화 및 테스트 데이터 재주입
uv run python tests/seed_data.py

# 3. 기존 동일한 테스트 Job이 중복 에러를 내지 않도록 K8s 상에서 삭제
kubectl delete job test-fds-batch-v1

# 4. FDS 배치 템플릿(CronJob) 클러스터 반영
kubectl apply -f k8s/fds-batch-cronjob.yaml

# 5. CronJob 설계를 기반으로 즉각 실행되는 1회성 Job 실행
kubectl create job --from=cronjob/fds-report-batch test-fds-batch-v1

# 6. 생성된 배치 Pod 로그 모니터링
kubectl logs -l job-name=test-fds-batch-v1 -f
```
</details>

---

## 5. 🚚 Auto-PO (자동 발주) 배치 테스트 (원클릭 자동화)

`auto_po_batch.py`는 재고가 안전재고 이하로 떨어졌을 때 출판사별로 발주서를 자동 생성합니다. 중복 발주 방지(멱등성) 로직이 내장되어 있습니다.

### 🚀 원클릭 통합 자동화 테스트 실행 방법
백엔드 터미널(`wms-core-backend`) 환경에서 아래 명령어를 실행합니다.
```powershell
# K8s 시크릿 배포 ➡️ 기존 AUTO_PO 삭제 ➡️ 시나리오 시딩 ➡️ K8s 수동 잡 생성 ➡️ 로그 스트리밍 ➡️ DB 발주 검증 조회 수행
powershell -ExecutionPolicy Bypass -File .\scripts\run_auto_po_batch_test.ps1
```

<details>
<summary>🔍 (참고용) 스크립트 내부에서 수행하는 수동 명령어 일람</summary>

```powershell
# 1. 과거의 더미 발주 데이터를 삭제하여 깨끗한 상태로 설정
python scripts/cleanup_auto_po.py

# 2. 테스트용 기초 데이터(재고 부족 도서 정보 등)를 DB에 시딩
python tests/seed_data.py

# 3. Auto-PO 배치 크론잡 템플릿을 클러스터에 배포
kubectl apply -f k8s/auto-po-batch-cronjob.yaml

# 4. 1회성 수동 실행 Job 생성
kubectl create job --from=cronjob/auto-po-batch test-auto-po-v1

# 5. 실행된 파드의 정상 작동 로그 모니터링
kubectl logs -l job-name=test-auto-po-v1 -f

# 6. DB에 발주가 정상 분할되어 들어갔는지 검증 스크립트로 최종 확인
python tests/test_auto_po_db.py
```
</details>
