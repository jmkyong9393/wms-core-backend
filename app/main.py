from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


from app.api.routes import (
    admin,
    admin_users,
    auth,
    certificates,
    db,
    inspections,
    inventory,
    mock,
    orders,
    outbound,
    stream,
)
from app.core.config import settings
from app.core.database import init_db
from app.core.exceptions import AppException

app = FastAPI(title=settings.PROJECT_NAME)


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
app.include_router(admin_users.router, prefix="/api/v1/admin/users", tags=["Admin Users"])

# AI 검수 API
app.include_router(inspections.router, prefix="/api/v1/inspections", tags=["Inspections"])
app.include_router(stream.router, prefix="/api/v1/inspections", tags=["Inspections Stream"])

# WMS 업무 API
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
app.include_router(outbound.router, prefix="/api/outbound", tags=["Outbound"])
app.include_router(certificates.router, prefix="/api/certificate", tags=["Certificate"])

# 관리자 및 개발 지원 API
app.include_router(admin.router, prefix="/api/v1/admin", tags=["Admin"])
app.include_router(db.router, prefix="/api/db", tags=["Database"])
app.include_router(mock.router, prefix="/api/mock", tags=["Mock"])



@app.get("/")
def read_root():
    return {"message": "Welcome to B2B WMS Platform API"}
