from typing import List
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class PickRequest(BaseModel):
    order_id: UUID


class PickingListItem(BaseModel):
    book_id: UUID
    location: str


class PickResponse(BaseModel):
    recommended_box: str
    picking_list: List[PickingListItem]


@router.post("/pick", response_model=PickResponse)
def pick_order(request: PickRequest):
    return PickResponse(
        recommended_box="2호",
        picking_list=[
            PickingListItem(
                book_id=UUID("00000000-0000-0000-0000-000000000001"),
                location="A-1-3",
            )
        ],
    )
