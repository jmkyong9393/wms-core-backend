"""노드별 구간 지연과 LLM 토큰·비용을 수집한다.

그래프 등록 시점에 노드 함수를 감싸기만 하며, 결과는 state의 node_timings /
node_tokens에 누적된다. 계측 실패가 검수를 막지 않도록 예외는 삼킨다.
"""

import contextvars
import functools
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# 콜백은 전역 인스턴스라 호출 노드를 알 수 없어 contextvar로 잇는다.
_current_node: contextvars.ContextVar = contextvars.ContextVar("wms_current_node", default=None)
_token_sink: contextvars.ContextVar = contextvars.ContextVar("wms_token_sink", default=None)

# 1K 토큰당 단가(USD)
_PRICE = {
    "gpt-4o": {"in": 0.0025, "out": 0.010},
    "gpt-4o-mini": {"in": 0.00015, "out": 0.0006},
}


def _cost(model: str, prompt: int, completion: int) -> float:
    # "gpt-4o"가 "gpt-4o-mini"의 접두사라 긴 키부터 대조한다.
    for key in sorted(_PRICE, key=len, reverse=True):
        if key in (model or ""):
            p = _PRICE[key]
            return prompt / 1000 * p["in"] + completion / 1000 * p["out"]
    return 0.0


class TokenCollector(BaseCallbackHandler):
    """노드별 LLM 토큰 사용량을 수집하는 LangChain 콜백.

    ChatOpenAI의 callbacks 필드가 타입 검증을 하므로 BaseCallbackHandler를 상속해야 한다.
    """

    def on_llm_end(self, response, **kwargs):
        try:
            out = getattr(response, "llm_output", None) or {}
            usage = out.get("token_usage") or {}
            model = out.get("model_name") or ""
            if not usage:
                # 일부 경로는 generations[].message.usage_metadata에만 담긴다
                for gen in getattr(response, "generations", None) or []:
                    for g in gen:
                        um = getattr(getattr(g, "message", None), "usage_metadata", None)
                        if um:
                            usage = {
                                "prompt_tokens": um.get("input_tokens", 0),
                                "completion_tokens": um.get("output_tokens", 0),
                                "total_tokens": um.get("total_tokens", 0),
                            }
                            break
            if not usage:
                return
            sink: List[dict] = _token_sink.get()
            if sink is None:
                return
            p = int(usage.get("prompt_tokens") or 0)
            c = int(usage.get("completion_tokens") or 0)
            sink.append(
                {
                    "model": model,
                    "prompt": p,
                    "completion": c,
                    "total": int(usage.get("total_tokens") or (p + c)),
                    "cost_usd": round(_cost(model, p, c), 6),
                }
            )
        except Exception as e:
            logger.debug(f"[계측] 토큰 수집 실패({type(e).__name__}) - 무시한다")


token_collector = TokenCollector()

# 컨테이너 TZ가 UTC이므로 기록 시각은 KST로 변환한다.
_KST = timezone(timedelta(hours=9))


def _now_kst_iso() -> str:
    return datetime.now(_KST).replace(tzinfo=None).isoformat(timespec="seconds")


def instrument(name: str, fn: Callable) -> Callable:
    """노드 함수를 감싸 구간 지연과 토큰 사용량을 state에 덧붙인다."""

    @functools.wraps(fn)
    def wrapped(state: Dict[str, Any], *args, **kwargs):
        t0 = time.perf_counter()
        # 이 노드에서 일어난 LLM 호출만 담길 수집함. 콜백이 contextvar로 찾아온다.
        sink: List[dict] = []
        tok_a = _token_sink.set(sink)
        node_a = _current_node.set(name)
        try:
            out = fn(state, *args, **kwargs)
        finally:
            _token_sink.reset(tok_a)
            _current_node.reset(node_a)

        ms = int((time.perf_counter() - t0) * 1000)
        if not isinstance(out, dict):
            return out

        try:
            out.setdefault("node_timings", [])
            out["node_timings"] = [
                *out["node_timings"],
                {"node": name, "ms": ms, "at": _now_kst_iso()},
            ]
            # 토큰을 쓰지 않은 노드는 기록하지 않는다
            if sink:
                out.setdefault("node_tokens", [])
                out["node_tokens"] = [
                    *out["node_tokens"],
                    {
                        "node": name,
                        "prompt": sum(x["prompt"] for x in sink),
                        "completion": sum(x["completion"] for x in sink),
                        "total": sum(x["total"] for x in sink),
                        "cost_usd": round(sum(x["cost_usd"] for x in sink), 6),
                        "calls": len(sink),
                        "models": sorted({x["model"] for x in sink if x.get("model")}),
                    },
                ]
        except Exception as e:
            logger.warning(f"[계측] state 적재 실패({type(e).__name__}) - 검수는 계속한다: {e}")
        return out

    return wrapped


def summarize(timings, tokens) -> Dict[str, Any]:
    """노드별 계측 목록을 합계로 집계한다."""
    by_node: Dict[str, Dict[str, Any]] = {}
    for t in timings or []:
        n = t.get("node")
        if not n:
            continue
        d = by_node.setdefault(n, {"ms": 0, "runs": 0, "tokens": 0, "cost_usd": 0.0, "calls": 0})
        d["ms"] += int(t.get("ms") or 0)
        d["runs"] += 1
    for k in tokens or []:
        n = k.get("node")
        if not n:
            continue
        d = by_node.setdefault(n, {"ms": 0, "runs": 0, "tokens": 0, "cost_usd": 0.0, "calls": 0})
        d["tokens"] += int(k.get("total") or 0)
        d["cost_usd"] = round(d["cost_usd"] + float(k.get("cost_usd") or 0.0), 6)
        d["calls"] += int(k.get("calls") or 0)
    return {
        "by_node": by_node,
        "total_ms": sum(v["ms"] for v in by_node.values()),
        "total_tokens": sum(v["tokens"] for v in by_node.values()),
        "total_cost_usd": round(sum(v["cost_usd"] for v in by_node.values()), 6),
        "llm_calls": sum(v["calls"] for v in by_node.values()),
    }
