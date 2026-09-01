"""Vision 구조화 출력 Pydantic 스키마"""
# ruff: noqa: F401,F403
import base64
import json
import os
import re

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image, ImageDraw, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ultralytics import YOLO
from ..rag.critic_cases import (
    CRITIC_PROMPT_VERSION,
    evaluate_with_precedents,
)
from ..rag.policy_search import (
    UBCI_POLICY_VERSION,
    search_policy_rules,
)

from ..state import Grade, WMSInspectionState

from app.ai.agents.common import *  # noqa: F401,F403





NormalizedValue = Annotated[float, Field(ge=0, le=1)]



DefectCode = Literal[
    "COVER_SCRATCH",
    "COVER_TEAR",
    "STICKER_MARK",
    "CORNER_CRUSH",
    "EDGE_WEAR",
    "SPINE_CRACKING",
    "LOOSE_BINDING",
    "GENERAL_STAIN",
    "FADING",
    "SIGNATURE",
    "LIBRARY_STAMP",
    "WATER_DAMAGE",
    "PAGE_WARPING",
    "PAGE_FOLD",
    "WRITING",
    "HIGHLIGHTING",
    "BARCODE_DAMAGE",
    "OTHER_VISIBLE_DAMAGE",
]



DefectLocation = Literal[
    "FRONT_COVER",
    "BACK_COVER",
    "SPINE",
    "CORNER",
    "BOOK_EDGE",
    "INNER_PAGE",
    "IDENTIFIER_AREA",
    "OTHER",
]




class DefectOutput(BaseModel):
    model_config = ConfigDict(strict=True)

    type: DefectCode
    location: DefectLocation
    bbox: list[NormalizedValue] = Field(
        min_length=4,
        max_length=4,
    )
    ratio: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    image_index: int = Field(ge=0)
    text_overlap: bool = False
    morphology_severe: bool = False
    observation: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_bbox(self):
        x_min, y_min, x_max, y_max = self.bbox

        if x_min >= x_max or y_min >= y_max:
            raise ValueError(
                "BBOX 좌표 순서가 올바르지 않습니다."
            )

        return self




RejectReason = Literal[
    "BACKGROUND_OBJECT",
    "COVER_DESIGN",
    "PRINTED_CONTENT",
    "LIGHT_OR_SHADOW",
    "INSUFFICIENT_EVIDENCE",
]



ReviewDecision = Literal[
    "CONFIRMED",
    "REJECTED",
    "UNCERTAIN",
]




class CandidateReview(BaseModel):
    """YOLO 결함 후보에 대한 VLM 검토 결과."""

    model_config = ConfigDict(strict=True)

    candidate_id: int = Field(ge=0)
    decision: ReviewDecision

    confirmed_type: DefectCode | None = None
    location: DefectLocation | None = None

    # 결함 존재 여부가 아닌 현재 판정의 확실성
    review_confidence: float = Field(ge=0, le=1)

    reject_reason: RejectReason | None = None
    text_overlap: bool = False
    morphology_severe: bool = False

    @model_validator(mode="after")
    def normalize_review(self):
        # 낮은 신뢰도의 자동 확정·자동 거절 차단
        if (
            self.review_confidence
            < MIN_VISION_CONFIDENCE
        ):
            self.decision = "UNCERTAIN"
            self.reject_reason = (
                "INSUFFICIENT_EVIDENCE"
            )

        confirmed_is_valid = (
            self.confirmed_type is not None
            and self.location is not None
            and self.reject_reason is None
        )

        if (
            self.decision == "CONFIRMED"
            and confirmed_is_valid
        ):
            return self

        if self.decision == "CONFIRMED":
            self.decision = "UNCERTAIN"

        self.confirmed_type = None
        self.location = None
        self.reject_reason = (
            self.reject_reason
            or "INSUFFICIENT_EVIDENCE"
        )

        return self




class HybridVisionReview(BaseModel):
    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    reviews: list[CandidateReview]
    missed_defect_suspected: bool
    review_confidence: float = Field(ge=0, le=1)




class FullImageVisionReview(BaseModel):
    """학습셋이 없는 사진을 전체 판독한 결과."""

    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    defects: list[DefectOutput] = Field(max_length=20)
    observations: list[
        Annotated[str, Field(max_length=500)]
    ] = Field(default_factory=list, max_length=20)
    review_confidence: float = Field(ge=0, le=1)




class CombinedDefectReview(BaseModel):
    model_config = ConfigDict(strict=True)

    defect_index: int = Field(ge=0)
    decision: ReviewDecision
    review_confidence: float = Field(ge=0, le=1)
    explanation: str = Field(min_length=1, max_length=500)
    printed_content_only: bool = False




class CombinedVisionReview(BaseModel):
    """두 경로가 합류한 뒤 수행하는 독립 검증 결과."""

    model_config = ConfigDict(strict=True)

    image_quality_ok: bool
    reviews: list[CombinedDefectReview] = Field(max_length=30)
    missed_defect_suspected: bool
    review_confidence: float = Field(ge=0, le=1)
