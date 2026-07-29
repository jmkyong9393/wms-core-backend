from datetime import datetime

from pydantic import BaseModel, Field


class RejectedItemsDiscardResponse(BaseModel):
    discarded_count: int = Field(description="폐기 완료 처리된 C Zone 도서 수")
    discarded_at: datetime = Field(description="일괄 폐기 완료 시각")

