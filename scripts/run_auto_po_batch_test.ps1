# run_auto_po_batch_test.ps1
# WMS Auto-PO Batch Job 수동 테스트 및 모니터링 자동화 스크립트

Write-Host "1. K8s Secret 리소스 배포 중..." -ForegroundColor Cyan
kubectl apply -f k8s/wms-master-secret.yaml
if (Test-Path "k8s/fds-secret.yaml") {
    kubectl apply -f k8s/fds-secret.yaml
}

Write-Host "`n2. 기존 더미 발주 데이터(AUTO_PO) 삭제 중..." -ForegroundColor Cyan
uv run python scripts/cleanup_auto_po.py

Write-Host "`n3. 테스트 시나리오 데이터 DB 주입 중 (uv run)..." -ForegroundColor Cyan
uv run python tests/seed_data.py

Write-Host "`n4. 기존 완료된 K8s Job 및 Pod 청소 중..." -ForegroundColor Cyan
kubectl delete jobs --all

Write-Host "`n5. auto-po-batch-cronjob.yaml 리소스 등록 중..." -ForegroundColor Cyan
kubectl apply -f k8s/auto-po-batch-cronjob.yaml

Write-Host "`n6. 1회성 테스트 Job(test-auto-po-v1) 생성 및 실행..." -ForegroundColor Cyan
kubectl create job --from=cronjob/auto-po-batch test-auto-po-v1

Write-Host "`n7. Pod 생성 대기 중..." -ForegroundColor Cyan
Start-Sleep -Seconds 2

# Pod 이름 찾기
$podName = ""
for ($i = 1; $i -le 10; $i++) {
    $podInfo = kubectl get pods -l job-name=test-auto-po-v1 -o jsonpath='{.items[0].metadata.name}' 2>$null
    if ($podInfo) {
        $podName = $podInfo
        Write-Host "   -> Pod 발견: $podName" -ForegroundColor Green
        break
    }
    Write-Host "   Pod가 생성되기를 기다리는 중... ($i/10)"
    Start-Sleep -Seconds 1
}

if (-not $podName) {
    Write-Error "Pod가 생성되지 않았습니다. K8s 클러스터 상태를 확인해 주세요."
    exit 1
}

Write-Host "`n8. Pod 상태 모니터링 (종료될 때까지 로그 스트리밍)..." -ForegroundColor Cyan
kubectl logs -f $podName

Write-Host "`n9. DB 발주 데이터 생성 여부 조회 검증 중..." -ForegroundColor Cyan
uv run python tests/test_auto_po_db.py

Write-Host "`n[SUCCESS] Auto-PO 배치 잡 실행 및 모니터링 완료!" -ForegroundColor Green
