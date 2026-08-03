# run_fds_batch_test.ps1
# WMS FDS Batch Job 수동 테스트 및 모니터링 자동화 스크립트

Write-Host "1. 기존 완료된 K8s Job 및 Pod 청소 중..." -ForegroundColor Cyan
kubectl delete jobs --all

Write-Host "`n2. fds-batch-cronjob.yaml 리소스 등록 중..." -ForegroundColor Cyan
kubectl apply -f k8s/fds-batch-cronjob.yaml

Write-Host "`n3. 1회성 테스트 Job(test-fds-batch-v1) 생성 및 실행..." -ForegroundColor Cyan
kubectl create job --from=cronjob/fds-report-batch test-fds-batch-v1

Write-Host "`n4. Pod 생성 대기 중..." -ForegroundColor Cyan
Start-Sleep -Seconds 2

# Pod 이름 찾기
$podName = ""
for ($i = 1; $i -le 10; $i++) {
    $podInfo = kubectl get pods -l job-name=test-fds-batch-v1 -o jsonpath='{.items[0].metadata.name}' 2>$null
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

Write-Host "`n5. Pod 상태 모니터링 (종료될 때까지 로그 스트리밍)..." -ForegroundColor Cyan
# logs -f를 사용해 실행이 완료될 때까지 실시간 로그 모니터링
kubectl logs -f $podName

Write-Host "`n[SUCCESS] FDS 배치 잡 실행 및 모니터링 완료!" -ForegroundColor Green
