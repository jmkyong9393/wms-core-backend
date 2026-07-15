from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.api.routes import (
    admin,
    certificates,
    db,
    inspections,
    inventory,
    mock,
    orders,
    outbound,
    returns,
    stream,
)

app = FastAPI(title=settings.PROJECT_NAME)

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

app.include_router(orders.router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(returns.router, prefix="/api/returns", tags=["Returns"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])

app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(stream.router, prefix="/api/stream", tags=["Stream"])

app.include_router(db.router, prefix="/api/db", tags=["Database"])
app.include_router(mock.router, prefix="/api/mock", tags=["Mock"])
app.include_router(inspections.router, prefix="/api/v1/inspections", tags=["Inspections"])
app.include_router(outbound.router, prefix="/api/v1/outbound", tags=["Outbound"])
app.include_router(certificates.router, prefix="/api/v1/certificate", tags=["Certificate"])


@app.get("/")
def read_root():
    return {"message": "Welcome to B2B WMS Platform API"}
