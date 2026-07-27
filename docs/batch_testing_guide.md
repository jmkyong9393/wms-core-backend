# WMS AI 플랫폼 배치 작업(CronJob) 로컬 테스트 가이드

이 문서는 로컬 개발 환경(Docker + Kind)에서 K8s CronJob으로 설정된 파이썬 배치 스크립트(`auto_po_batch`, `report_batch`)를 테스트하는 방법을 안내합니다.

---

## 1. 공통 준비 사항 (사전 작업)

배치 작업들은 K8s 컨테이너 내부에서 실행되므로, 필요한 환경 변수(DB 접속 정보 등)를 담은 **Secret** 리소스가 클러스터에 등록되어 있어야 합니다.

```powershell
# 1. DB 및 설정 시크릿 적용
kubectl apply -f k8s/fds-secret.yaml
kubectl apply -f k8s/wms-master-secret.yaml

### 🔐 Secret 리소스 구성 형태 (참고용)
테스트를 위해서는 아래와 같은 형태의 Secret 파일들이 작성되어 있어야 합니다.

**1. `fds-secret.yaml` (DB 접속 정보 등)**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: fds-db-secret
  namespace: default
type: Opaque
stringData:
  DATABASE_URL: "postgresql://admin:password@host.docker.internal:5432/wms_db"
```

**2. `wms-master-secret.yaml` (초기 마스터 계정 설정값)**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: wms-master-secret
  namespace: default
type: Opaque
stringData:
  INITIAL_MASTER_EMPLOYEE_ID: "M1001"
  INITIAL_MASTER_NAME: "최고관리자"
  INITIAL_MASTER_PASSWORD: "RealPassword123!"
```

# 2. 파이썬 코드가 변경되었다면 도커 이미지를 새로 빌드하고 Kind 클러스터에 주입합니다.
docker build -t wms-ai:latest .
.\kind-windows-amd64.exe load docker-image wms-ai:latest --name wms-cluster
```

> [!WARNING]
> 파이썬 코드(`.py`)를 수정한 후에는 반드시 위 2번 항목(도커 빌드 및 클러스터 주입)을 수행해야 변경된 코드가 K8s 환경에 반영됩니다!

---

## 2. Auto-PO (자동 발주) 배치 테스트

`auto_po_batch.py`는 재고가 안전재고 이하로 떨어졌을 때 출판사별로 발주서를 생성합니다. 멱등성 로직이 적용되어 있어, **이미 발주(Pending)가 진행 중인 경우 중복 발주를 하지 않습니다.**

### 테스트 시나리오
```powershell
# 1. (선택 사항) 과거의 더미 발주 데이터를 삭제하여 깨끗한 상태로 만듭니다.
python scripts/cleanup_auto_po.py

# 2. 테스트용 기초 데이터(재고 및 출고 이력)를 DB에 밀어 넣습니다.
# (이 스크립트 안에는 재고가 3개, 2개 등으로 설정되어 있어 자동 발주 조건을 충족합니다.)
python tests/seed_data.py

# 3. K8s CronJob(설계도)을 클러스터에 업데이트합니다.
kubectl apply -f k8s/auto-po-batch-cronjob.yaml

# 4. CronJob을 기반으로 1회성 테스트 Job을 즉시 생성하여 실행시킵니다.
# (잡 이름이 중복되면 에러가 나므로, 뒤에 v1, v2 등의 숫자를 붙여줍니다.)
kubectl create job --from=cronjob/auto-po-batch test-auto-po-v1

# 5. 실행된 파드의 로그를 확인합니다.
kubectl logs -l job-name=test-auto-po-v1

# 6. 파이썬 검증 스크립트로 DB에 발주가 제대로 갈라져 들어갔는지 확인합니다.
python tests/test_auto_po_db.py
```

---

## 3. FDS (이상 탐지 및 핫스팟 리포팅) 배치 테스트

`report_batch.py`는 물류 센터의 입출고 및 반품 데이터를 분석하여 핫스팟(지연 구간)과 이상 탐지(FDS) 리포트를 생성합니다.

### 테스트 시나리오
```powershell
# 1. K8s CronJob(설계도)을 클러스터에 업데이트합니다.
kubectl apply -f k8s/fds-batch-cronjob.yaml

# 2. CronJob을 기반으로 1회성 테스트 Job을 즉시 생성하여 실행시킵니다.
kubectl create job --from=cronjob/fds-report-batch test-fds-batch-v1

# 3. 파드가 생성되고 완료(Completed)될 때까지 로그를 확인합니다.
kubectl logs -l job-name=test-fds-batch-v1
```

> [!TIP]
> 다 쓴 테스트용 파드(Completed 상태)가 너무 많이 쌓여 클러스터가 지저분해졌다면 아래 명령어로 한 번에 청소할 수 있습니다.
> ```powershell
> kubectl delete jobs --all
> ```
