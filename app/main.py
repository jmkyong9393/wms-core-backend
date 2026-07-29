from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse



from app.api.routes import (
    admin,
    admin_users,
    auth,
    books,
    certificates,
    db,
    inbound,
    inspection_inventory,
    inspections,
    lpn,
    restock,
    inventory,
    mock,
    notifications,
    orders,
    outbound,
    rejected_items,
    stream,
    used_inbound,
)
from app.core.config import settings
from app.core.database import init_db
from app.core.exceptions import AppException

WMS_OPENAPI_TAGS = [
    {
        "name": "Books",
        "description": "ISBN 기반 도서 마스터 조회 API",
    },
    {
        "name": "Inbound",
        "description": "신간 입고, 중고 매입, 고객 반품 입고와 통합 입고 이력 조회 API",
    },
    {
        "name": "Inventory",
        "description": "신간 묶음 재고와 중고 단품 재고 통합 조회 API",
    },
    {
        "name": "Inspections",
        "description": (
            "중고·반품 도서의 AI 검수 요청, 상태 조회, 재검수 및 "
            "관리자 HITL 판정 API"
        ),
    },
    {
        "name": "LPN",
        "description": "중고·반품 단품 재고의 LPN 상세 조회 API",
    },
    {
        "name": "Certificate",
        "description": "공개 토큰 기반 UBCI 품질보증서 조회 API",
    },
    {
        "name": "Orders",
        "description": "신간 묶음 재고와 중고 LPN 단품 재고의 주문 생성 API",
    },
    {
        "name": "Outbound",
        "description": "신간 묶음 재고와 중고 LPN 단품 재고의 피킹 및 출고 처리 API",
    },
]

app = FastAPI(
    title=settings.PROJECT_NAME,
    description=(
        "B2B WMS의 도서 조회, 입고, 통합 재고 조회, 주문 및 "
        "동시성 제어 출고 API를 제공합니다."
    ),
    openapi_tags=WMS_OPENAPI_TAGS,
)


@app.exception_handler(AppException)
async def app_exception_handler(
    request: Request,
    exc: AppException,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "error_code": exc.error_code,
        },
        headers=exc.headers,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        # 이후 실제 프론트 URL 추가는 여기에!
    ],   
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

# 인증 및 관리자 API
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Auth"])
app.include_router(admin_users.router, prefix="/api/v1/users/admin", tags=["Admin Users"])

# AI 검수 API
app.include_router(inspections.router, prefix="/api/v1/inspections", tags=["Inspections"])
app.include_router(stream.router, prefix="/api/v1/inspections", tags=["Inspections Stream"])

# WMS 업무 API
app.include_router(books.router, prefix="/api/v1/books", tags=["Books"])
app.include_router(inbound.router, prefix="/api/v1/inbound", tags=["Inbound"])
app.include_router(used_inbound.router, prefix="/api/v1/inbound", tags=["Inbound"])
app.include_router(
    inspection_inventory.router,
    prefix="/api/v1/internal/inventory",
    tags=["Inventory"],
)
app.include_router(orders.router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(inventory.v1_router, prefix="/api/v1/inventory", tags=["Inventory"])
app.include_router(lpn.router, prefix="/api/v1/lpn", tags=["LPN"])
app.include_router(outbound.router, prefix="/api/v1/outbound", tags=["Outbound"])
app.include_router(certificates.router, prefix="/api/v1/certificate", tags=["Certificate"])
app.include_router(
    rejected_items.router,
    prefix="/api/v1/admin/rejected-items",
    tags=["Inventory"],
)

# 기존 개발용 재고 상태 API
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])

# 관리자 및 개발 지원 API
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(db.router, prefix="/api/db", tags=["Database"])
app.include_router(mock.router, prefix="/api/mock", tags=["Mock"])
app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["Notifications"],)

# 자동 발주 추천 Agent 임시 호출 api
app.include_router(restock.router, prefix="/api/v1/admin/restock", tags=["Admin Restock"],)



@app.get("/")
def read_root():
    return {"message": "Welcome to B2B WMS Platform API"}
