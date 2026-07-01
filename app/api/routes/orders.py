from fastapi import APIRouter

router = APIRouter()

@router.post("/")
def create_order():
    return {"message": "Order created successfully (Dummy)", "order_id": "dummy-uuid"}

@router.get("/")
def list_orders():
    return [{"order_id": "dummy-uuid", "status": "PENDING"}]
