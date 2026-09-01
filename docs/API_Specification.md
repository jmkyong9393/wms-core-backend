# API 명세서 (자동 생성)

> **이 문서는 손으로 고치지 않습니다.** FastAPI가 만드는 OpenAPI 스키마에서 생성하므로, 코드를 바꾸면 아래 명령으로 다시 뽑아 주세요.
>
> ```bash
> uv run python scripts/generate_api_spec.py
> ```
>
> 실행 중인 서버에서는 `http://localhost:8080/docs`(Swagger UI)로도 볼 수 있습니다.

- 생성일: 2026-09-02
- 엔드포인트: **70개** / 태그 18개

## 목차

- [기타](#기타) (1개)
- [Database](#database) (1개)
- [Mock](#mock) (4개)
- [Admin](#admin) (13개)
- [Inventory](#inventory) (4개)
- [Admin Restock](#admin-restock) (5개)
- [Auth](#auth) (5개)
- [Books](#books) (2개)
- [Certificate](#certificate) (1개)
- [Inbound](#inbound) (3개)
- [Inspections](#inspections) (6개)
- [Inspections Stream](#inspections-stream) (1개)
- [Pricing](#pricing) (3개)
- [LPN](#lpn) (4개)
- [Notifications](#notifications) (5개)
- [Orders](#orders) (2개)
- [Outbound](#outbound) (4개)
- [Admin Users](#admin-users) (6개)

## 기타

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/` | Read Root | - |

### `GET` /

**Read Root**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

## Database

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/db/health` | Check Database Health | - |

### `GET` /api/db/health

**Check Database Health**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

## Mock

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/mock/seed` | Seed Mock Data | 필요 |
| `POST` | `/api/mock/seed/order-outbound` | Seed Order Outbound Data | 필요 |
| `POST` | `/api/mock/seed/outbound-demo` | Seed Outbound Demo Data | 필요 |
| `GET` | `/api/mock/summary` | Get Mock Summary | 필요 |

### `POST` /api/mock/seed

**Seed Mock Data**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

### `POST` /api/mock/seed/order-outbound

**Seed Order Outbound Data**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

### `POST` /api/mock/seed/outbound-demo

**Seed Outbound Demo Data**

데모 출고 주문에 사용할 동일 도서의 신간·중고 재고를 준비한다.

신간은 묶음 Inventory로,
중고는 EXCELLENT 등급의 AVAILABLE LPN으로 생성된다.

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

### `GET` /api/mock/summary

**Get Mock Summary**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

## Admin

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/admin/dashboard/flow-trend` | 입출고 및 AI 검수 처리 시간 일별 추이 조회 | 필요 |
| `GET` | `/api/v1/admin/dashboard/inbound-summary` | 입고 통합 대시보드 요약 조회 | 필요 |
| `GET` | `/api/v1/admin/dashboard/outbound-summary` | 출고 통합 대시보드 요약 조회 | 필요 |
| `GET` | `/api/v1/admin/fds/policies` | FDS 룰셋 임계값 조회 | 필요 |
| `PUT` | `/api/v1/admin/fds/policies/{policy_key}` | FDS 룰 임계값 단일 조절 | 필요 |
| `GET` | `/api/v1/admin/fds/reports` | 이상거래 위험군 탐지 기록 조회 | 필요 |
| `GET` | `/api/v1/admin/inspection-metrics` | Get Inspection Metrics | 필요 |
| `GET` | `/api/v1/admin/inspections` | Get Admin Inspection History | 필요 |
| `GET` | `/api/v1/admin/inspections/hitl-queue` | HITL 처리 보드 목록 조회 | 필요 |
| `GET` | `/api/v1/admin/inspections/hitl-queue/metrics` | HITL 처리 보드 KPI 조회 | 필요 |
| `GET` | `/api/v1/admin/inspections/{job_id}` | Get Admin Inspection Detail | 필요 |
| `GET` | `/api/v1/admin/inspections/{job_id}/agent-logs` | 검수 Agent 단계별 실행 로그 조회 | 필요 |
| `GET` | `/api/v1/admin/weekly-insights` | 주간 누적 절감액 및 불량 분석 핫스팟 조회 | 필요 |

### `GET` /api/v1/admin/dashboard/flow-trend

**입출고 및 AI 검수 처리 시간 일별 추이 조회**

최근 지정 기간의 일별 입고·출고 수량과 완료된 AI 검수 건의 평균 처리 시간을 반환합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `days` | query | - | integer | 조회할 최근 일수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `DashboardFlowTrendResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/dashboard/inbound-summary

**입고 통합 대시보드 요약 조회**

금일 입고 수량, 검수 처리 현황, 최근 입고 추이, 중고·반품 검수 등급 분포, 구역별 가용 재고 및 최근 입고 내역을 반환합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `days` | query | - | integer | 입고 추이 조회 기간(일) |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InboundDashboardSummaryResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/dashboard/outbound-summary

**출고 통합 대시보드 요약 조회**

진행 중인 B2B 피킹 주문 수, 실제 바코드 스캔 기준 피킹 완료율, 당일 송장 발급 완료 건수와 최근 출고 주문 목록을 반환합니다. AUTO_PO 입고 주문은 출고 대시보드 집계에서 제외합니다.

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `OutboundDashboardSummaryResponse` |

### `GET` /api/v1/admin/fds/policies

**FDS 룰셋 임계값 조회**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `FdsPolicyResponse[]` |

### `PUT` /api/v1/admin/fds/policies/{policy_key}

**FDS 룰 임계값 단일 조절**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `policy_key` | path | O | string |  |

**요청 본문** (`application/json`): `FdsPolicyUpdateRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `policy_value` | O | number |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `FdsPolicyResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/fds/reports

**이상거래 위험군 탐지 기록 조회**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `FdsReportResponse[]` |

### `GET` /api/v1/admin/inspection-metrics

**Get Inspection Metrics**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

### `GET` /api/v1/admin/inspections

**Get Admin Inspection History**

관리자 검수 이력 그리드를 서버 페이지네이션 방식으로 조회한다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `status` | query | - | ReturnJobStatus | 검수 상태 필터(APPROVED, FAILED 등) |
| `start_date` | query | - | string | 조회 시작 날짜 |
| `end_date` | query | - | string | 조회 종료 날짜 |
| `keyword` | query | - | string | 도서명 검색 키워드 |
| `grade` | query | - | ConditionGrade | 확정 검수 등급 필터(MINT, EXCELLENT, NORMAL, REJECT) |
| `fast_track` | query | - | boolean | Fast Track 여부 필터. true면 Fast Track 건만, false면 일반 검수 건만 조회 |
| `reason_code` | query | - | string | AI 판정 사유 코드 필터 |
| `page` | query | - | integer | 페이지 번호 |
| `size` | query | - | integer | 페이지당 조회 건수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InspectionHistoryListResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/inspections/hitl-queue

**HITL 처리 보드 목록 조회**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `bucket` | query | - | HITLQueueBucket | 보드 구분값: PENDING, IN_REVIEW, RECHECK, COMPLETED |
| `page` | query | - | integer | 해당 보드의 페이지 번호 |
| `size` | query | - | integer | 해당 보드에 한 번에 표시할 카드 수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `HITLQueueListResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/inspections/hitl-queue/metrics

**HITL 처리 보드 KPI 조회**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `HITLQueueMetricsResponse` |

### `GET` /api/v1/admin/inspections/{job_id}

**Get Admin Inspection Detail**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InspectionDetailResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/inspections/{job_id}/agent-logs

**검수 Agent 단계별 실행 로그 조회**

검수 작업에 저장된 Vision, Policy, Critic, Report Agent의 단계별 실행 로그를 반환합니다. 관리자 또는 MASTER 권한이 필요합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `AgentLogStep[]` |
| 401 | 인증 토큰이 없거나 유효하지 않음 | - |
| 403 | 관리자 또는 MASTER 권한이 없음 | - |
| 404 | 검수 작업을 찾을 수 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/weekly-insights

**주간 누적 절감액 및 불량 분석 핫스팟 조회**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `WeeklyInsightResponse[]` |

## Inventory

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/admin/rejected-items/discard-all` | C Zone 폐기 대기 도서 일괄 처리 | 필요 |
| `POST` | `/api/v1/internal/inventory/inspection-results` | 검수 결과 기반 LPN 재고 또는 폐기 대기 편입 | - |
| `GET` | `/api/v1/inventory` | 단품 및 묶음 재고 통합 조회 | 필요 |
| `GET` | `/api/v1/inventory/{inventory_id}` | 신간 묶음 재고 단건 조회 | 필요 |

### `POST` /api/v1/admin/rejected-items/discard-all

**C Zone 폐기 대기 도서 일괄 처리**

관리자가 C Zone의 모든 REJECT_HOLD 도서를 폐기 완료 처리합니다. 처리 대상 행을 잠근 뒤 레코드는 삭제하지 않고 DISCARDED 상태와 처리 시각을 남깁니다. 이미 처리된 도서는 다시 집계하지 않습니다.

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `RejectedItemsDiscardResponse` |
| 401 | 인증 토큰이 없거나 유효하지 않음 | - |
| 403 | MASTER 또는 ADMIN 권한이 없음 | - |

### `POST` /api/v1/internal/inventory/inspection-results

**검수 결과 기반 LPN 재고 또는 폐기 대기 편입**

AI 검수 결과를 입고 품목에 적용합니다. 승인 결과는 UBCI 정책으로 등급을 계산하여 B Zone 판매 가능 단품 재고에 즉시 편입하고, 반려 결과는 C Zone의 폐기 대기 레코드로 분리합니다.

**요청 본문** (`application/json`): `InspectionInventoryRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `return_job_id` | O | string | 재고 편입의 근거가 되는 검수 작업 ID |
| `decision` | O | `APPROVE` | `REJECT` | AI 최종 승인 또는 반려 결정 |
| `ubci_score` | - | number | string | AI가 계산한 최종 UBCI 점수 |
| `defects` | - | object[] | 등급 정책의 치명적 결함 판정에 사용할 결함 목록 |
| `admin_decision_code` | - | HITLReasonCode | 관리자 HITL 판정 사유 코드 |
| `final_grade` | - | ConditionGrade | 관리자가 확정한 최종 도서 등급 |
| `rejection_disposition` | - | `REJECT_RETURN` | `REJECT_DISCARD` | 반려 도서 처리 방식 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InspectionInventoryResponse` |
| 404 | 검수 작업을 찾을 수 없음 | - |
| 409 | 검수·입고 상태 충돌 또는 가용 로케이션 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/inventory

**단품 및 묶음 재고 통합 조회**

신간 묶음 재고와 중고·반품 LPN 단품 재고를 통합 조회합니다. ISBN을 전달하면 해당 도서의 로케이션별 재고만 조회합니다. 관리자 또는 MASTER 권한이 필요합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `isbn` | query | - | string | 특정 ISBN의 로케이션별 재고만 조회 |
| `keyword` | query | - | string | 도서명 또는 ISBN 부분 검색 |
| `grade` | query | - | ConditionGrade | 재고 등급 필터. 신간 묶음 재고는 MINT로 조회된다. |
| `zone` | query | - | string | 보관 구역 필터 예: A, B, C |
| `start_date` | query | - | string | 마지막 변경일 기준 조회 시작일(YYYY-MM-DD) |
| `end_date` | query | - | string | 마지막 변경일 기준 조회 종료일(YYYY-MM-DD) |
| `page` | query | - | integer | 페이지 번호 |
| `size` | query | - | integer | 페이지당 조회 건수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InventoryListResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/inventory/{inventory_id}

**신간 묶음 재고 단건 조회**

재고 ID를 기준으로 신간 묶음 재고의 도서, 로케이션, 가용 수량과 현재 판매 가격을 조회합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `inventory_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InventoryListItemResponse` |
| 404 | 신간 묶음 재고를 찾을 수 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

## Admin Restock

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/admin/restock/mock-recommendation` | 자동 발주 추천 Agent 수동 호출(개발 확인용) | 필요 |
| `GET` | `/api/v1/admin/restock/proposals` | Restock 추천안 목록 조회 | 필요 |
| `GET` | `/api/v1/admin/restock/proposals/{proposal_id}` | Restock 추천안 상세 조회 | 필요 |
| `POST` | `/api/v1/admin/restock/proposals/{proposal_id}/approve` | Restock 추천안 승인 및 AUTO_PO 생성 | 필요 |
| `POST` | `/api/v1/admin/restock/proposals/{proposal_id}/reject` | Restock 추천안 반려 | 필요 |

### `POST` /api/v1/admin/restock/mock-recommendation

**자동 발주 추천 Agent 수동 호출(개발 확인용)**

Restock Agent를 수동으로 호출해 추천 결과만 확인하는 개발용 API.

실제 업무 흐름에서는 최종 반려된 검수 작업을 기준으로
Worker가 Restock 추천안을 생성하고 OrderProposal에 저장한다.

이 API는 OrderProposal 저장, 관리자 알림, AUTO_PO 생성은 수행하지 않는다.

**요청 본문** (`application/json`): `RestockRecommendationRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `isbn` | O | string | 반려된 도서의 ISBN |
| `bookTitle` | O | string | 반려된 도서명 |
| `recentSalesQuantity` | O | integer | 임시 최근 판매량 |
| `currentStock` | O | integer | 임시 현재 가용 재고 |
| `pendingAutoPoQuantity` | - | integer | 진행 중인 AUTO_PO 입고 예정 수량 |
| `rejectedQuantity` | O | integer | 이번 검수에서 반려된 수량 |
| `rejectionReasonCode` | O | string | 반려 사유 코드 |
| `proposalSource` | - | RestockProposalSource |  |
| `safetyStockQuantity` | - | integer | 안전재고 기준 수량. 반려 대체 발주에서는 null |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `RestockRecommendationResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/restock/proposals

**Restock 추천안 목록 조회**

현재 관리자 테넌트의 Restock 추천안을 최신 생성 순으로 조회한다.

상태를 지정하면 해당 상태의 추천안만 반환한다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `status` | query | - | OrderProposalStatus | 추천안 상태 필터(PENDING, APPROVED, REJECTED, NOT_REQUIRED) |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `RestockProposalListItemResponse[]` |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/admin/restock/proposals/{proposal_id}

**Restock 추천안 상세 조회**

관리자 검토에 필요한 추천안의 Agent 입력값, 근거, 처리 이력을 조회한다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `proposal_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `RestockProposalDetailResponse` |
| 404 | 추천안을 찾을 수 없거나 다른 테넌트의 추천안 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/admin/restock/proposals/{proposal_id}/approve

**Restock 추천안 승인 및 AUTO_PO 생성**

PENDING 추천안을 승인하고 잔여 수량이 있으면 AUTO_PO 주문을 생성한다.

승인 시점에 새 AUTO_PO가 추가된 경우 해당 수량을 제외해,
이미 충분하면 NOT_REQUIRED 상태로 처리한다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `proposal_id` | path | O | string |  |

**요청 본문** (`application/json`): `RestockProposalReviewRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `comment` | - | string | 관리자 승인 또는 반려 의견 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `RestockProposalReviewResponse` |
| 404 | 추천안을 찾을 수 없음 | - |
| 409 | 이미 처리된 추천안이거나 추가 발주가 필요 없거나, 신간 적치 가능 로케이션이 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/admin/restock/proposals/{proposal_id}/reject

**Restock 추천안 반려**

PENDING 추천안을 반려 처리한다.

반려된 추천안은 AUTO_PO 주문을 생성하지 않는다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `proposal_id` | path | O | string |  |

**요청 본문** (`application/json`): `RestockProposalReviewRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `comment` | - | string | 관리자 승인 또는 반려 의견 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `RestockProposalReviewResponse` |
| 404 | 추천안을 찾을 수 없음 | - |
| 409 | 이미 처리되었거나 반려할 수 없는 추천안 | - |
| 422 | Validation Error | `HTTPValidationError` |

## Auth

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | Login | - |
| `POST` | `/api/v1/auth/logout` | Logout | - |
| `GET` | `/api/v1/auth/me` | Get Me | 필요 |
| `PATCH` | `/api/v1/auth/password` | Update Password | 필요 |
| `POST` | `/api/v1/auth/refresh` | Refresh Access Token | - |

### `POST` /api/v1/auth/login

**Login**

**요청 본문** (`application/json`): `LoginRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `employee_id` | O | string |  |
| `password` | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `TokenResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/auth/logout

**Logout**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 204 | Successful Response | - |

### `GET` /api/v1/auth/me

**Get Me**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `UserResponse` |

### `PATCH` /api/v1/auth/password

**Update Password**

**요청 본문** (`application/json`): `PasswordChangeRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `current_password` | O | string |  |
| `new_password` | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 204 | Successful Response | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/auth/refresh

**Refresh Access Token**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `TokenResponse` |

## Books

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/books/register` | ISBN으로 도서 마스터 등록 | - |
| `GET` | `/api/v1/books/{isbn}` | ISBN으로 도서 기초 정보 조회 | - |

### `POST` /api/v1/books/register

**ISBN으로 도서 마스터 등록**

스캔한 ISBN이 이미 등록되어 있으면 기존 도서를 반환합니다. 미등록 ISBN은 알라딘 OpenAPI에서 제목, 출판사, 정가와 카테고리를 조회하고 WMS 내부 카테고리로 변환하여 도서 마스터에 등록합니다.

**요청 본문** (`application/json`): `BookRegistrationRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `isbn` | O | string | 스캔한 ISBN-10 또는 ISBN-13 바코드 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `BookRegistrationResponse` |
| 404 | 알라딘에서 ISBN을 찾을 수 없음 | - |
| 422 | ISBN 또는 카테고리를 WMS 정책으로 처리할 수 없음 | - |
| 502 | 알라딘 OpenAPI가 잘못된 응답을 반환함 | - |
| 503 | 알라딘 OpenAPI 설정 누락 또는 통신 실패 | - |

### `GET` /api/v1/books/{isbn}

**ISBN으로 도서 기초 정보 조회**

바코드로 스캔한 ISBN을 이용해 도서 마스터 정보를 조회합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `isbn` | path | O | string | 조회할 10~13자리 ISBN 바코드 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `BookLookupResponse` |
| 404 | 등록되지 않은 ISBN | `object` |
| 422 | Validation Error | `HTTPValidationError` |

## Certificate

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/certificate/{token}` | 공개 토큰 기반 UBCI 품질보증서 조회 | - |

### `GET` /api/v1/certificate/{token}

**공개 토큰 기반 UBCI 품질보증서 조회**

LPN 라벨의 QR에 포함된 공개 토큰으로 품질보증서를 조회합니다. DB에 저장된 최신 확정 검수 결과를 반환하며, 인증 없이 접근할 수 있는 소비자 공개 API입니다. LPN, 로케이션, 내부 작업 로그는 노출하지 않습니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `token` | path | O | string | 입고 시 발급된 공개 품질보증서 토큰 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `CertificateResponse` |
| 404 | 조회 가능하거나 발급이 완료된 품질보증서가 없음 | `object` |
| 422 | Validation Error | `HTTPValidationError` |

## Inbound

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/inbound/history` | 최근 입고 이력 조회 | - |
| `POST` | `/api/v1/inbound/new-stock` | ISBN 기반 신간 묶음 입고 및 로케이션 확정 | - |
| `POST` | `/api/v1/inbound/used-item` | 중고·반품 도서 입고 접수 및 LPN 발급 | - |

### `GET` /api/v1/inbound/history

**최근 입고 이력 조회**

최근 입고 작업을 생성 시각 역순으로 조회합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `limit` | query | - | integer | 조회할 최대 입고 작업 수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InboundHistoryItemResponse[]` |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/inbound/new-stock

**ISBN 기반 신간 묶음 입고 및 로케이션 확정**

동일 ISBN의 신간을 수량 단위로 입고합니다. 기존 신간 재고가 있으면 같은 로케이션을 재사용하고, 최초 입고일 때만 카테고리 기반 A Zone 로케이션을 확정합니다. 신간에는 LPN과 품질 등급을 발급하지 않습니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `Idempotency-Key` | header | - | string | 재요청 중복 입고를 방지하는 클라이언트 생성 UUID |

**요청 본문** (`application/json`): `NewStockInboundRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `isbn` | O | string | 외부 도서 API로 조회한 ISBN |
| `title` | O | string | 도서명 |
| `publisher` | - | string | 출판사명 |
| `category` | O | BookCategory | 내부 기준으로 변환된 도서 카테고리 |
| `base_price` | O | number | string | 도서 기준 판매가 |
| `standard_size` | - | StandardSize | 3D Bin Packing용 도서 규격 |
| `thickness_mm` | - | integer | 도서 두께(mm) |
| `quantity` | O | integer | 이번에 입고할 동일 ISBN 신간 수량 |
| `supplier_name` | - | string | 신간 공급처명 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 201 | Successful Response | `NewStockInboundResponse` |
| 409 | Idempotency-Key 충돌 또는 적재 가능한 로케이션 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/inbound/used-item

**중고·반품 도서 입고 접수 및 LPN 발급**

중고 매입 또는 고객 반품 도서 1권을 검수 대기 상태로 접수하고 물리적 단품 추적용 LPN을 발급합니다. 이 단계에서는 판매 가능 재고에 편입하지 않습니다. 동일 Idempotency-Key 재요청은 기존 입고 품목과 LPN을 반환합니다.응답의 label_scan_url이 실제 물리 라벨 QR에 인코딩된다.certificate_url은 소비자용 품질보증서 직접 조회 URL이다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `Idempotency-Key` | header | - | string | 재요청 중복 입고를 방지하는 클라이언트 생성 UUID |

**요청 본문** (`application/json`): `UsedBookInboundRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `inbound_type` | O | `USED_PURCHASE` | `CUSTOMER_RETURN` | 중고 매입 또는 고객 반품 입고 유형 |
| `book_id` | O | string | 검수할 도서 마스터 ID |
| `supplier_name` | - | string | 중고 매입 공급자명. 고객 반품에서는 생략 가능 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 201 | Successful Response | `UsedBookInboundResponse` |
| 404 | 도서 마스터를 찾을 수 없음 | - |
| 409 | Idempotency-Key가 다른 입고 요청에 사용됨 | - |
| 422 | Validation Error | `HTTPValidationError` |

## Inspections

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/inspections` | 중고·반품 도서 AI 검수 요청 | 필요 |
| `GET` | `/api/v1/inspections/{job_id}` | AI 검수 상태 및 결과 조회 | 필요 |
| `POST` | `/api/v1/inspections/{job_id}/hitl` | 관리자 검수 판정 및 후속 처리 요청 | 필요 |
| `POST` | `/api/v1/inspections/{job_id}/hitl/start` | HITL 검토 시작 | 필요 |
| `POST` | `/api/v1/inspections/{job_id}/recheck` | 재촬영 이미지 등록 및 AI 재검수 요청 | 필요 |
| `POST` | `/api/v1/inspections/{job_id}/stream-ticket` | 검수 상태 SSE 구독 티켓 발급 | 필요 |

### `POST` /api/v1/inspections

**중고·반품 도서 AI 검수 요청**

LPN이 발급된 입고 품목과 여러 CloudFront 이미지 URL을 검증하여 검수 작업을 생성하고, Celery 비동기 AI 파이프라인에 등록합니다. 생성된 작업의 상태는 조회 API 또는 SSE로 확인할 수 있습니다.

**요청 본문** (`application/json`): `CreateInspectionRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `inbound_item_id` | O | string |  |
| `book_id` | O | string |  |
| `mode` | O | InspectionMode |  |
| `image_paths` | O | string[] | S3 업로드 완료 후 조회 가능한 CloudFront 이미지 URL 목록. 도서 한 권의 대표·측면·내지·결함 이미지를 촬영 순서대로 전달합니다. |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 202 | Successful Response | `CreateInspectionResponse` |
| 404 | 입고 품목 또는 로케이션을 찾을 수 없음 | - |
| 409 | 입고·검수 상태 또는 요청 정보가 충돌함 | - |
| 422 | CloudFront 이미지 URL 형식이 올바르지 않음 | - |
| 503 | 검수 작업을 비동기 큐에 등록하지 못함 | - |

### `GET` /api/v1/inspections/{job_id}

**AI 검수 상태 및 결과 조회**

검수 작업의 진행 상태, 진행률, UBCI 점수, 품질 등급, 최종 리포트와 검수에 사용된 CloudFront 이미지 URL 목록을 조회합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `InspectionStatusResponse` |
| 404 | 현재 사용자의 검수 작업을 찾을 수 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/inspections/{job_id}/hitl

**관리자 검수 판정 및 후속 처리 요청**

AI가 자동 판정하지 못한 검수 작업에 관리자가 승인, 등급 하향, 반송, 폐기 또는 재촬영 결정을 제출합니다. 판정 결과를 저장하고 필요한 WMS 후속 작업을 비동기 큐에 등록합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**요청 본문** (`application/json`): `HITLDecisionRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `action` | O | HITLAction |  |
| `reviewer_reason_code` | O | HITLReasonCode |  |
| `target_grade` | - | HITLTargetGrade |  |
| `comment` | - | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 202 | Successful Response | `HITLDecisionResponse` |
| 404 | 관리 대상 검수 작업을 찾을 수 없음 | - |
| 409 | 검수 상태와 관리자 판정 요청이 충돌함 | - |
| 422 | Validation Error | `HTTPValidationError` |
| 503 | 관리자 판정 후속 작업을 등록하지 못함 | - |

### `POST` /api/v1/inspections/{job_id}/hitl/start

**HITL 검토 시작**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `HITLReviewStartResponse` |
| 404 | 검수 작업을 찾을 수 없음 | - |
| 409 | 이미 다른 관리자가 검토 중이거나 HITL 대상이 아님 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/inspections/{job_id}/recheck

**재촬영 이미지 등록 및 AI 재검수 요청**

재촬영이 요구된 검수 작업에 새로운 CloudFront 이미지 URL 목록을 등록합니다. 기존 이미지 목록을 교체하고 작업을 PENDING으로 되돌린 뒤 Celery 비동기 AI 재검수 파이프라인에 등록합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**요청 본문** (`application/json`): `RecheckInspectionRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `image_paths` | O | string[] | S3 재업로드 완료 후 조회 가능한 CloudFront 이미지 URL 목록. 기존 검수 이미지를 대체할 대표·측면·내지·결함 이미지를 촬영 순서대로 전달합니다. |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 202 | Successful Response | `RecheckInspectionResponse` |
| 404 | 재검수 대상 작업을 찾을 수 없음 | - |
| 409 | 현재 상태에서 재검수를 요청할 수 없음 | - |
| 422 | CloudFront 이미지 URL 형식이 올바르지 않음 | - |
| 503 | 재검수 작업을 비동기 큐에 등록하지 못함 | - |

### `POST` /api/v1/inspections/{job_id}/stream-ticket

**검수 상태 SSE 구독 티켓 발급**

현재 사용자가 소유한 검수 작업의 실시간 상태를 SSE로 구독할 수 있도록 일회성 티켓과 티켓이 포함된 스트림 URL, 만료 시간을 반환합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `StreamTicketResponse` |
| 404 | 현재 사용자의 검수 작업을 찾을 수 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

## Inspections Stream

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/inspections/{job_id}/stream` | Stream Inspection Status | - |

### `GET` /api/v1/inspections/{job_id}/stream

**Stream Inspection Status**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `job_id` | path | O | string |  |
| `ticket` | query | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | - |
| 422 | Validation Error | `HTTPValidationError` |

## Pricing

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/internal/pricing/results` | LPN 동적 가격 산정 결과 저장 | - |
| `GET` | `/api/v1/internal/pricing/{lpn_barcode}/context` | LPN 동적 가격 책정 컨텍스트 조회 | - |
| `POST` | `/api/v1/internal/pricing/{lpn_barcode}/recalculate` | LPN 동적 가격 재산정 | - |

### `POST` /api/v1/internal/pricing/results

**LPN 동적 가격 산정 결과 저장**

Rule 기반 동적 가격 Agent가 산정한 할인율과 최종 판매가격을 중고·반품 LPN 단품 재고에 저장합니다. 동일 LPN에 대한 재산정 결과는 기존 값을 갱신합니다.

**요청 본문** (`application/json`): `DynamicPricingResultRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `lpn_barcode` | O | string | 가격 산정 대상 LPN 바코드 |
| `discount_rate` | O | number | string | 백엔드 저장용 소수 할인율 |
| `final_price` | O | number | string | Agent가 산정한 최종 판매가격 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `DynamicPricingResultResponse` |
| 404 | 등록된 LPN 단품 재고를 찾을 수 없음 | - |
| 409 | 정가·UBCI 미확정 또는 가격 정책 범위를 벗어난 결과 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/internal/pricing/{lpn_barcode}/context

**LPN 동적 가격 책정 컨텍스트 조회**

Rule 기반 동적 가격 Agent가 사용할 원천 데이터를 조회합니다. 가격을 직접 계산하지 않고 도서 정가, 내부 카테고리, UBCI 점수와 확정 품질 등급을 반환합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `lpn_barcode` | path | O | string | 가격 책정 대상 중고·반품 단품의 LPN 바코드 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `DynamicPricingContextResponse` |
| 404 | 등록된 LPN 단품 재고를 찾을 수 없음 | - |
| 409 | 정가 또는 UBCI 점수가 확정되지 않음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/internal/pricing/{lpn_barcode}/recalculate

**LPN 동적 가격 재산정**

가격 산정에 실패했거나 재산정이 필요한 판매 가능 LPN의 DB 컨텍스트를 이용해 Pricing Agent를 동기 실행하고 할인율과 판매가격을 갱신합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `lpn_barcode` | path | O | string | 가격을 다시 산정할 중고·반품 단품의 LPN 바코드 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `DynamicPricingResultResponse` |
| 404 | 등록된 LPN 단품 재고를 찾을 수 없음 | - |
| 409 | 판매 불가능 상태이거나 정가·UBCI가 확정되지 않음 | - |
| 422 | Validation Error | `HTTPValidationError` |

## LPN

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/lpn/print` | 네트워크 라벨 프린터 직접 출력 | 필요 |
| `GET` | `/api/v1/lpn/scan/{scan_value}` | 작업자용 LPN QR 스캔 상세 조회 | 필요 |
| `GET` | `/api/v1/lpn/{lpn_barcode}` | LPN 단품 재고 상세 조회 | 필요 |
| `POST` | `/api/v1/lpn/{lpn_barcode}/labels/{label_type}/reprint` | 작업자용 LPN·UBCI 라벨 재출력 | 필요 |

### `POST` /api/v1/lpn/print

**네트워크 라벨 프린터 직접 출력**

프론트엔드 모달에서 입력된 정보(LPN 바코드, 도서명, ISBN, 작업자ID)를 기반으로 ZPL 템플릿을 렌더링한 후, 창고 내 LAN 네트워크 라벨 프린터로 직접 TCP 소켓 전송을 수행합니다.

**요청 본문** (`application/json`): `PrintLpnRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `lpnBarcode` | O | string |  |
| `title` | O | string |  |
| `isbn` | O | string |  |
| `workerId` | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `PrintLpnResponse` |
| 401 | 인증 토큰이 없거나 유효하지 않음 | - |
| 403 | WMS 작업자 권한이 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |
| 500 | 프린터 TCP 연결 실패 또는 네트워크 오류 | - |

### `GET` /api/v1/lpn/scan/{scan_value}

**작업자용 LPN QR 스캔 상세 조회**

작업자 모바일 앱이 LPN 라벨 QR의 스캔 토큰으로 입고·검수·재촬영·재고 편입 상태와 현재 로케이션을 조회합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `scan_value` | path | O | string | LPN 바코드 또는 품질보증서 QR 토큰 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `LpnScanResponse` |
| 401 | 인증 토큰이 없거나 유효하지 않음 | - |
| 403 | WMS 작업자 권한이 없음 | - |
| 404 | LPN 스캔 토큰을 찾을 수 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/lpn/{lpn_barcode}

**LPN 단품 재고 상세 조회**

작업자가 스캔한 LPN을 기준으로 중고·반품 단품 재고의 도서 정보, 품질 등급, UBCI 점수, 현재 상태와 보관 로케이션을 조회합니다. 창고 내부 정보이므로 WORKER, ADMIN, MASTER 권한이 필요합니다. 고객 공개용 품질 정보는 Certificate API를 사용합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `lpn_barcode` | path | O | string | 조회할 물리 도서의 LPN 바코드 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `LpnDetailResponse` |
| 401 | 인증 토큰이 없거나 유효하지 않음 | - |
| 403 | WMS 작업자 권한이 없음 | - |
| 404 | 등록된 LPN 단품 재고를 찾을 수 없음 | - |
| 422 | Validation Error | `HTTPValidationError` |
| 500 | LPN에 연결된 도서 또는 로케이션 데이터가 유실됨 | - |

### `POST` /api/v1/lpn/{lpn_barcode}/labels/{label_type}/reprint

**작업자용 LPN·UBCI 라벨 재출력**

작업자가 프린터 오류, 용지 걸림, 라벨 훼손 등의 사유로 LPN 또는 UBCI 라벨을 재출력합니다. 재출력은 프린터 전송만 수행하며 입고·검수·재고 데이터를 변경하지 않습니다. UBCI 라벨은 검수 승인 후 판매 가능 단품 재고가 존재하는 경우에만 출력할 수 있습니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `lpn_barcode` | path | O | string | 재출력할 물리 단품 LPN |
| `label_type` | path | O | LabelType | 재출력할 라벨 유형(LPN 또는 UBCI) |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `LabelReprintResponse` |
| 401 | 인증 토큰이 없거나 유효하지 않음 | - |
| 403 | WMS 작업자 권한이 없음 | - |
| 404 | LPN 라벨 원본 데이터를 찾을 수 없음 | - |
| 409 | UBCI 라벨 출력 조건 미충족 또는 출고 완료 단품 | - |
| 422 | Validation Error | `HTTPValidationError` |

## Notifications

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/notifications` | 내 알림 목록 조회 | 필요 |
| `PATCH` | `/api/v1/notifications/read-all` | 전체 알림 읽음 처리 | 필요 |
| `GET` | `/api/v1/notifications/stream` | 알림센터 SSE 스트림 연결 | - |
| `POST` | `/api/v1/notifications/stream-ticket` | 알림센터 SSE 연결 티켓 발급 | 필요 |
| `PATCH` | `/api/v1/notifications/{notification_id}/read` | 개별 알림 읽음 처리 | 필요 |

### `GET` /api/v1/notifications

**내 알림 목록 조회**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `limit` | query | - | integer | 조회할 최대 알림 수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `NotificationListResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `PATCH` /api/v1/notifications/read-all

**전체 알림 읽음 처리**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `MarkAllNotificationsReadResponse` |

### `GET` /api/v1/notifications/stream

**알림센터 SSE 스트림 연결**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `ticket` | query | O | string | POST /stream-ticket으로 발급받은 단기 SSE 티켓 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/notifications/stream-ticket

**알림센터 SSE 연결 티켓 발급**

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `NotificationStreamTicketResponse` |

### `PATCH` /api/v1/notifications/{notification_id}/read

**개별 알림 읽음 처리**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `notification_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `MarkNotificationReadResponse` |
| 422 | Validation Error | `HTTPValidationError` |

## Orders

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/orders` | 출고 대상 주문 목록 조회 | 필요 |
| `POST` | `/api/v1/orders` | 신간 및 등급별 중고 B2B 주문 생성 | - |

### `GET` /api/v1/orders

**출고 대상 주문 목록 조회**

출고 담당자가 피킹할 B2B 주문 목록을 조회합니다. 기본적으로 PENDING 주문을 조회하며, status를 지정하면 PICKING 또는 SHIPPED 주문도 조회할 수 있습니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `status` | query | - | OrderStatus | 주문 상태 필터 |
| `page` | query | - | integer |  |
| `size` | query | - | integer |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `OrderListResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/orders

**신간 및 등급별 중고 B2B 주문 생성**

book_id, 수량과 선택적 condition_grade를 기준으로 주문을 생성합니다. 등급이 없으면 신간 묶음 재고, 등급이 있으면 해당 등급의 중고 단품 재고 주문입니다. 신간은 수량을 묶어서 저장하고, 중고는 물리적 책 한 권당 quantity=1인 주문 품목으로 분리합니다. LPN 선택은 출고 피킹 시 수행하며, 현재 가격은 도서 기준가를 사용하고 UBCI 동적 가격은 적용하지 않습니다.

**요청 본문** (`application/json`): `CreateOrderRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `customer_name` | O | string | B2B 고객사명 |
| `customer_id` | - | string | 고객 고유 ID. 현재 B2B MVP에서는 선택 사항 |
| `logistics_center` | - | string | 주문을 처리할 물류 거점 식별자 |
| `items` | O | OrderItemRequest[] | 신간 묶음 또는 등급별 중고 단품 주문 품목 목록 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `CreateOrderResponse` |
| 404 | 주문 품목의 도서 마스터를 찾을 수 없음 | - |
| 409 | 주문 품목에 유효한 기준 가격이 없음 | - |
| 422 | REJECT 등급 또는 유효하지 않은 요청 | - |

## Outbound

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `POST` | `/api/v1/outbound/picking-instructions` | 출고 피킹 지시서 생성 및 재고 예약 | 필요 |
| `GET` | `/api/v1/outbound/picking-instructions/{order_id}` | 피킹 지시서 상세 및 스캔 진행 상태 조회 | 필요 |
| `POST` | `/api/v1/outbound/picking-instructions/{order_id}/confirm` | 피킹 완료 주문 출고 확정 및 송장 발급 | 필요 |
| `POST` | `/api/v1/outbound/picking-instructions/{order_id}/scan` | 피킹 예약 품목 바코드 스캔 확인 | 필요 |

### `POST` /api/v1/outbound/picking-instructions

**출고 피킹 지시서 생성 및 재고 예약**

PENDING 주문의 재고를 실제 차감하지 않고 예약합니다. 신간은 reserved_quantity를 증가시키고, 중고 단품은 RESERVED로 변경합니다. 피킹 지시서는 Zone, Rack, Shelf 순서로 그룹화됩니다. 실제 재고 차감과 SHIPPED 처리는 출고 확정 단계에서 수행합니다.

**요청 본문** (`application/json`): `PickRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `order_id` | O | string | 피킹 지시서를 생성할 PENDING 주문 ID |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `PickResponse` |
| 404 | 출고 주문 또는 재고 로케이션을 찾을 수 없음 | - |
| 409 | 주문 상태 오류, 주문 품목 없음 또는 가용 재고 부족 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `GET` /api/v1/outbound/picking-instructions/{order_id}

**피킹 지시서 상세 및 스캔 진행 상태 조회**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `order_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `PickingInstructionDetailResponse` |
| 404 | 주문을 찾을 수 없음 | - |
| 409 | 아직 피킹 지시서가 생성되지 않은 주문 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/outbound/picking-instructions/{order_id}/confirm

**피킹 완료 주문 출고 확정 및 송장 발급**

PICKING 상태 주문에서 모든 예약 품목의 ISBN 또는 LPN 스캔이 완료된 경우에만 출고를 확정합니다. 신간은 실제 수량과 예약 수량을 함께 차감하고, 중고 LPN은 RESERVED에서 SHIPPED로 변경합니다. 출고 로그와 송장을 생성한 뒤 주문 상태를 SHIPPED로 변경합니다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `order_id` | path | O | string |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `ShipmentConfirmResponse` |
| 404 | 출고 주문을 찾을 수 없음 | - |
| 409 | 출고 확정 불가능한 주문 상태 또는 예약 재고 불일치 | - |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/outbound/picking-instructions/{order_id}/scan

**피킹 예약 품목 바코드 스캔 확인**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `order_id` | path | O | string |  |

**요청 본문** (`application/json`): `PickingScanRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `allocation_type` | O | PickingAllocationType | 스캔 대상 예약 재고 유형. 신간 묶음 재고는 NEW_STOCK, 중고 단품은 USED_ITEM |
| `allocation_id` | O | string | 피킹 지시서 상세에서 받은 예약 Allocation ID. 스캔 대상 로케이션·품목을 정확히 식별한다. |
| `barcode` | O | string | 신간은 ISBN, 중고 단품은 예약된 LPN 바코드 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `PickingScanResponse` |
| 404 | 주문 또는 피킹 예약 품목을 찾을 수 없음 | - |
| 409 | 주문 상태 오류, 바코드 불일치, 이미 완료된 신간 예약 수량 또는 중고 LPN 상태 오류 | - |
| 422 | Validation Error | `HTTPValidationError` |

## Admin Users

| Method | Path | 설명 | 인증 |
|---|---|---|---|
| `GET` | `/api/v1/users/admin` | MASTER 직원 계정 목록 조회 | 필요 |
| `POST` | `/api/v1/users/admin/bulk-create` | MASTER 직원 계정 엑셀 일괄 생성 | 필요 |
| `GET` | `/api/v1/users/admin/bulk-template` | MASTER 직원 일괄 생성 엑셀 양식 다운로드 | 필요 |
| `POST` | `/api/v1/users/admin/create-accounts` | Create Employee Account | 필요 |
| `PATCH` | `/api/v1/users/admin/{user_id}/role` | Change User Role | 필요 |
| `PATCH` | `/api/v1/users/admin/{user_id}/status` | Change User Status | 필요 |

### `GET` /api/v1/users/admin

**MASTER 직원 계정 목록 조회**

현재 MASTER와 같은 테넌트에 속한 직원 계정을 조회한다.

목록의 id는 권한·상태 변경 API에서 사용하는 User UUID이며,
비밀번호와 Refresh Token 같은 민감한 인증 정보는 노출하지 않는다.

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `keyword` | query | - | string | 사번·이름·이메일 부분 검색 |
| `role` | query | - | UserRole | 역할 필터 |
| `status` | query | - | UserStatus | 계정 상태 필터 |
| `page` | query | - | integer | 페이지 번호 |
| `size` | query | - | integer | 페이지당 조회 건수 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `EmployeeListResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `POST` /api/v1/users/admin/bulk-create

**MASTER 직원 계정 엑셀 일괄 생성**

직원 정보를 엑셀로 업로드하여 한 번에 생성한다.

생성 성공 시 사번과 최초 비밀번호가 담긴 결과 엑셀을 반환한다.
최초 비밀번호는 DB에 저장하지 않으며 이 응답 파일에서만 제공한다.

**요청 본문** (`multipart/form-data`): `Body_create_employee_accounts_bulk_api_v1_users_admin_bulk_create_post`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `file` | O | string | 이름 \| 입사일 \| 역할 \| 이메일 헤더를 가진 .xlsx 직원 일괄 생성 파일 |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 201 | Successful Response | `object` |
| 422 | 엑셀 형식, 개별 행 값 또는 이메일 중복 검증 실패 | - |

### `GET` /api/v1/users/admin/bulk-template

**MASTER 직원 일괄 생성 엑셀 양식 다운로드**

직원 일괄 생성에 사용할 빈 엑셀 양식을 다운로드한다.

입력 헤더:
이름 | 입사일 | 역할 | 이메일

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `object` |

### `POST` /api/v1/users/admin/create-accounts

**Create Employee Account**

**요청 본문** (`application/json`): `EmployeeCreateRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `name` | O | string |  |
| `email` | - | string |  |
| `hire_date` | O | string |  |
| `role` | - | `ADMIN` | `WORKER` |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 201 | Successful Response | `EmployeeCreateResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `PATCH` /api/v1/users/admin/{user_id}/role

**Change User Role**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `user_id` | path | O | string |  |

**요청 본문** (`application/json`): `UserRoleUpdateRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `role` | O | `ADMIN` | `WORKER` |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `UserResponse` |
| 422 | Validation Error | `HTTPValidationError` |

### `PATCH` /api/v1/users/admin/{user_id}/status

**Change User Status**

**요청 파라미터**

| 이름 | 위치 | 필수 | 타입 | 설명 |
|---|---|---|---|---|
| `user_id` | path | O | string |  |

**요청 본문** (`application/json`): `UserStatusUpdateRequest`

| 필드 | 필수 | 타입 | 설명 |
|---|---|---|---|
| `status` | O | UserStatus |  |

**응답**

| 코드 | 설명 | 본문 |
|---|---|---|
| 200 | Successful Response | `UserResponse` |
| 422 | Validation Error | `HTTPValidationError` |

