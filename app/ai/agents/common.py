"""공용 상수·트레이스 유틸 (agents.py 분할 이동, 로직 불변)"""

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

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

logger = logging.getLogger(__name__)

POLICY_VERSION = UBCI_POLICY_VERSION


load_dotenv()


# 신뢰도가 이 값보다 낮으면 자동 처리하지 않고 재검토
MIN_VISION_CONFIDENCE = float(os.getenv("MIN_VISION_CONFIDENCE", "0.80"))


MIN_POLICY_CONFIDENCE = float(os.getenv("MIN_POLICY_CONFIDENCE", "0.75"))


VLM_CROP_MIN_SIDE = int(os.getenv("VLM_CROP_MIN_SIDE", "256"))


VLM_CROP_CONTEXT_SCALE = float(os.getenv("VLM_CROP_CONTEXT_SCALE", "1.8"))


MAX_INSPECTION_IMAGE_PIXELS = int(
    os.getenv(
        "MAX_INSPECTION_IMAGE_PIXELS",
        "40000000",
    )
)


# 프론트 검수 이미지의 고정 순서
IMAGE_VIEWS = (
    "FRONT",
    "BACK",
    "INNER",
)


# 라우팅 기준은 각 촬영 유형의 학습셋 보유 여부입니다.
TRAINED_MODEL_VIEWS = frozenset({"FRONT", "BACK"})


UNTRAINED_VLM_VIEWS = frozenset({"INNER"})


def trace_event(
    event: str,
    payload: dict,
) -> None:
    """에이전트의 입력·출력을 한 줄 JSON으로 표시한다."""

    logger.info(
        "[AI_TRACE] %s",
        json.dumps(
            {
                "event": event,
                **payload,
            },
            ensure_ascii=False,
            default=str,
        ),
    )


def summarize_score_breakdown(
    score_breakdown: list[dict] | None,
) -> str:
    """관리자 로그에 표시할 결함별 감점 요약."""

    summaries = []

    for item in score_breakdown or []:
        if not isinstance(item, dict):
            continue

        if item.get("fatal"):
            penalty = "즉시반려"
        else:
            penalty = f"-{item.get('applied_penalty')}점"

        summaries.append(f"{item.get('type', 'UNKNOWN')} {penalty}")

    return ", ".join(summaries) or "감점 없음"


def normalize_model_class(value: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "_",
        value.strip().lower(),
    )
    return normalized.strip("_")


def resolve_model_path(
    configured_path: str,
) -> Path:
    # agents 패키지 분할로 파일 깊이가 1단계 깊어짐(app/ai/agents/common.py)
    # → parents[3]이 레포 루트(= models/의 부모)다.
    repo_root = Path(__file__).resolve().parents[3]
    path = Path(configured_path)

    if path.is_absolute():
        return path

    return repo_root / path
