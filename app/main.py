from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.api.routes import db, inventory, mock, orders, returns

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

app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(returns.router, prefix="/api/returns", tags=["Returns"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
app.include_router(db.router, prefix="/api/db", tags=["Database"])
app.include_router(mock.router, prefix="/api/mock", tags=["Mock"])

@app.get("/")
def read_root():
    return {"message": "Welcome to B2B WMS Platform API"}
