"""模型分级 client —— Haiku/Sonnet 调用 + structured JSON 输出 + 成本统计（Design §3 / §8c）。

设计要点：
- 模型分级（Proposal §2）：Haiku 高频小推理 / Sonnet 合成出题复盘。
- **优雅降级**：未装 anthropic SDK 或无 ANTHROPIC_API_KEY 时，`available=False`，
  `complete_structured()` 抛 `LLMUnavailable`，由各 agent 捕获后回退到 Phase 1 stub，
  从而保证"链路永远通"（不要求功能跑通）。
- 结构化输出：指示模型只返回 JSON，提取并用 Pydantic 校验（第一版，不调优）。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from ..contracts.enums import ModelTier
from ..contracts.message import TokenUsage

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """无可用 LLM（缺 SDK 或 API key），调用方应回退到 stub。"""


class LLMStructuredError(RuntimeError):
    """结构化输出解析失败（多次重试后）。"""


# 模型映射（可经环境变量覆盖）
_MODEL_NAME = {
    ModelTier.HAIKU: os.getenv("LF_HAIKU_MODEL", "claude-3-5-haiku-latest"),
    ModelTier.SONNET: os.getenv("LF_SONNET_MODEL", "claude-3-5-sonnet-latest"),
}

# 每百万 token 价格（USD）——粗略,仅用于成本统计（Design §9a）
_PRICE_PER_MTOK = {
    ModelTier.HAIKU: {"prompt": 0.80, "completion": 4.00},
    ModelTier.SONNET: {"prompt": 3.00, "completion": 15.00},
}


@dataclass
class LLMResult:
    text: str
    tokens: TokenUsage
    cost_usd: float
    model_tier: ModelTier


def _estimate_cost(tier: ModelTier, tokens: TokenUsage) -> float:
    p = _PRICE_PER_MTOK[tier]
    return (tokens.prompt * p["prompt"] + tokens.completion * p["completion"]) / 1_000_000


def _extract_json(text: str) -> str:
    """从模型输出里提取首个 JSON 对象（容忍 ```json 代码块包裹）。"""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    brace = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if brace:
        return brace.group(1)
    return text.strip()


class LLMClient:
    """统一模型调用入口。无 key 时 available=False。"""

    def __init__(self) -> None:
        self._client = None
        self.available = False
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            try:
                import anthropic  # type: ignore

                self._client = anthropic.Anthropic(api_key=api_key)
                self.available = True
            except Exception:
                self.available = False

    def complete(
        self,
        prompt: str,
        model_tier: ModelTier = ModelTier.HAIKU,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        timeout_s: Optional[float] = None,
    ) -> LLMResult:
        if not self.available or self._client is None:
            raise LLMUnavailable("ANTHROPIC_API_KEY 未设置或 anthropic SDK 不可用。")

        kwargs = {
            "model": _MODEL_NAME[model_tier],
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if timeout_s:
            kwargs["timeout"] = timeout_s

        resp = self._client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        usage = TokenUsage(
            prompt=getattr(resp.usage, "input_tokens", 0),
            completion=getattr(resp.usage, "output_tokens", 0),
        )
        return LLMResult(
            text=text,
            tokens=usage,
            cost_usd=_estimate_cost(model_tier, usage),
            model_tier=model_tier,
        )

    def complete_structured(
        self,
        prompt: str,
        schema: Type[T],
        model_tier: ModelTier = ModelTier.HAIKU,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        timeout_s: Optional[float] = None,
        retries: int = 1,
    ) -> Tuple[T, LLMResult]:
        """调用 LLM 并把 JSON 输出解析为 `schema`。失败重试 1 次（Design §8a）。"""
        schema_hint = (
            "\n\n严格只输出一个 JSON 对象，匹配以下字段（不要解释、不要 markdown 之外内容）：\n"
            f"{json.dumps(_schema_fields(schema), ensure_ascii=False)}"
        )
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            result = self.complete(
                prompt + schema_hint,
                model_tier=model_tier,
                system=system,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
            )
            try:
                obj = schema.model_validate_json(_extract_json(result.text))
                return obj, result
            except Exception as e:  # noqa: BLE001 - 解析失败则重试
                last_err = e
                time.sleep(0)  # 占位：真实现可加退避
        raise LLMStructuredError(f"结构化解析失败：{last_err}")


def _schema_fields(schema: Type[BaseModel]) -> dict:
    """给模型一个轻量字段提示（不暴露完整 JSON Schema，第一版够用）。"""
    fields = {}
    for name, info in schema.model_fields.items():
        ann = getattr(info.annotation, "__name__", str(info.annotation))
        fields[name] = ann
    return fields


# 全局单例。
LLM = LLMClient()
