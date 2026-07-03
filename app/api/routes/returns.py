from fastapi import APIRouter

router = APIRouter()

@router.post("/upload")
def upload_return_image():
    pass

@router.get("/{task_id}")
def get_return_status(task_id: str):
    pass
