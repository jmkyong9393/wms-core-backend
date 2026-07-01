from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
def get_inventory_status():
    return {
        "total_books": 1500,
        "total_locations": 200,
        "recent_transactions": [
            {"type": "INBOUND", "quantity": 10},
            {"type": "OUTBOUND", "quantity": 2},
        ]
    }
