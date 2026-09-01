"""HITL 중단점 노드"""
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




def human_node(state: WMSInspectionState) -> WMSInspectionState:
    """
    6. Human-In-The-Loop (HITL) 노드
    TODO: Critic이 반복해서 Policy를 반려하거나 확신할 수 없는 예외 케이스(Outlier)일 경우, 관리자의 수동 개입을 대기합니다.
    - 주의: 이 노드는 MemorySaver에 의해 일시 정지(Pause)를 유발하는 용도이므로 빈 상태로 둡니다.
    """
    print("[Agent] HITL 노드 진입 - 관리자의 수동 개입(승인/수정) 대기 중")
    return {}
