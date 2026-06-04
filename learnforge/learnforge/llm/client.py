"""模型分级 client —— 经 OpenRouter 调用 + structured JSON 输出 + 成本统计（Design §3 / §8c）。

设计要点：
- **统一走 OpenRouter**（OpenAI 兼容网关）：所有 LLM 文本调用都打 `OPENROUTER_API_KEY`。
  好处：一个 key 覆盖 OpenAI / Anthropic / Google 等模型；切模型只需改 `LF_HAIKU_MODEL` /
  `LF_SONNET_MODEL`（例如想用 Claude 就设 `anthropic/claude-3.5-sonnet`）。
- **先用便宜模型**：两档默认都用 `openai/gpt-4o-mini`，需要更强再经 env 覆盖。
- **优雅降级**：无 `OPENROUTER_API_KEY`（或 httpx 不可用、请求失败）时，`available=False` /
  `complete()` 抛 `LLMUnavailable`，由各 agent 捕获后回退到确定性 stub —— 保证“链路永远通”。
- 结构化输出：指示模型只返回 JSON，提取并用 Pydantic 校验（第一版，不调优）。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from ..contracts.enums import ModelTier
from ..contracts.message import TokenUsage

# 从仓库根 .env 读取 OPENROUTER_API_KEY（key 只存在未提交的 .env，不入代码/配置）。
# 不依赖 python-dotenv：项目最小安装也应能读取本地密钥。
def _load_local_env() -> None:
    seen = set()
    for base in [Path.cwd(), *Path(__file__).resolve().parents]:
        env_path = base / ".env"
        if env_path in seen or not env_path.exists():
            continue
        seen.add(env_path)
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_local_env()

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    """无可用 LLM（缺 key / httpx / 请求失败），调用方应回退到 stub。"""


class LLMStructuredError(RuntimeError):
    """结构化输出解析失败（多次重试后）。"""


# OpenRouter chat/completions 端点（可经 env 覆盖，便于走代理）。
OPENROUTER_URL = os.getenv("LF_OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")

# 模型映射（先用便宜模型；可经环境变量覆盖为任意 OpenRouter 模型 id）。
_MODEL_NAME = {
    ModelTier.HAIKU: os.getenv("LF_HAIKU_MODEL", "openai/gpt-4o-mini"),
    # 先便宜：SONNET 档默认也用 gpt-4o-mini；要更强设 LF_SONNET_MODEL（如 anthropic/claude-3.5-sonnet）。
    ModelTier.SONNET: os.getenv("LF_SONNET_MODEL", "openai/gpt-4o-mini"),
}

# 每百万 token 价格（USD）——粗略，仅用于成本统计（Design §9a）。默认按 gpt-4o-mini。
_PRICE_PER_MTOK = {
    ModelTier.HAIKU: {"prompt": 0.15, "completion": 0.60},
    ModelTier.SONNET: {"prompt": 0.15, "completion": 0.60},
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
    """统一模型调用入口（OpenRouter）。无 key / httpx 不可用时 available=False。"""

    def __init__(self) -> None:
        self._api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        self._httpx = None
        self.available = False
        if self._api_key:
            try:
                import httpx  # type: ignore

                self._httpx = httpx
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
        response_format: Optional[dict] = None,
        model: Optional[str] = None,
        images: Optional[List[str]] = None,
    ) -> LLMResult:
        """`images`：图片 data URL 列表(vision)。非空 → user content 组装成 OpenAI 多模态数组
        (text + image_url)，交 vision 模型(如 gpt-4o/4o-mini)"看"图。需模型本身支持视觉。"""
        if not self.available or self._httpx is None:
            raise LLMUnavailable("OPENROUTER_API_KEY 未设置或 httpx 不可用。")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if images:
            user_content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": u}} for u in images if u
            ]
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model or _MODEL_NAME[model_tier],  # 显式 model 覆盖档位默认（如意图层用 gpt-5）
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            # JSON 模式：让模型保证输出合法 JSON（structured 调用用，提升嵌套 schema 成功率）。
            payload["response_format"] = response_format
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # OpenRouter 可选归因头（不影响功能）。
            "HTTP-Referer": "https://github.com/learnforge",
            "X-Title": "LearnForge",
        }
        try:
            resp = self._httpx.post(
                OPENROUTER_URL, headers=headers, json=payload, timeout=timeout_s or 30.0
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001 - 任何传输/解析失败都降级为 LLMUnavailable
            raise LLMUnavailable(f"OpenRouter 请求失败：{type(e).__name__}: {e}")

        if isinstance(data, dict) and data.get("error"):
            raise LLMUnavailable(f"OpenRouter error: {str(data['error'])[:200]}")

        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        tokens = TokenUsage(
            prompt=usage.get("prompt_tokens", 0) or 0,
            completion=usage.get("completion_tokens", 0) or 0,
        )
        return LLMResult(
            text=text,
            tokens=tokens,
            cost_usd=_estimate_cost(model_tier, tokens),
            model_tier=model_tier,
        )

    def complete_with_tools(
        self,
        messages: list,
        tools: list,
        model_tier: ModelTier = ModelTier.HAIKU,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        timeout_s: Optional[float] = None,
        model: Optional[str] = None,
        tool_choice: Optional[object] = None,
        temperature: Optional[float] = None,
    ) -> Tuple[dict, "LLMResult"]:
        """真正的 tool-calling 单回合（OpenAI 兼容）：返回 assistant 这一回合的 message + 用量。

        - `messages`：调用方维护的完整对话历史（含此前的 tool 结果）。
        - `tools`：OpenAI 格式工具列表（见 mcp.tools.to_openai_tools）。
        - 返回 (assistant_message, LLMResult)；assistant_message 可能带 `tool_calls`，
          由外层 ReactRunner 执行工具并把结果作为 role=tool 消息回灌后再次调用。
        循环、预算、权限门都在 ReactRunner，本方法只做单次传输。
        """
        if not self.available or self._httpx is None:
            raise LLMUnavailable("OPENROUTER_API_KEY 未设置或 httpx 不可用。")

        msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
        payload = {
            "model": model or _MODEL_NAME[model_tier],  # 显式 model 覆盖档位默认（如意图层用 gpt-4o）
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/learnforge",
            "X-Title": "LearnForge",
        }
        try:
            resp = self._httpx.post(
                OPENROUTER_URL, headers=headers, json=payload, timeout=timeout_s or 45.0
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            raise LLMUnavailable(f"OpenRouter tool 请求失败：{type(e).__name__}: {e}")
        if isinstance(data, dict) and data.get("error"):
            raise LLMUnavailable(f"OpenRouter error: {str(data['error'])[:200]}")

        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {"role": "assistant", "content": ""}
        usage = data.get("usage") or {}
        tokens = TokenUsage(
            prompt=usage.get("prompt_tokens", 0) or 0,
            completion=usage.get("completion_tokens", 0) or 0,
        )
        result = LLMResult(
            text=msg.get("content") or "",
            tokens=tokens,
            cost_usd=_estimate_cost(model_tier, tokens),
            model_tier=model_tier,
        )
        return msg, result

    def complete_structured(
        self,
        prompt: str,
        schema: Type[T],
        model_tier: ModelTier = ModelTier.HAIKU,
        system: Optional[str] = None,
        max_tokens: int = 1024,
        timeout_s: Optional[float] = None,
        retries: int = 1,
        model: Optional[str] = None,
        images: Optional[List[str]] = None,
    ) -> Tuple[T, LLMResult]:
        """调用 LLM 并把 JSON 输出解析为 `schema`。失败重试 1 次（Design §8a）。

        `model`：可选模型 id 覆盖（如意图层用 gpt-5），缺省按 `model_tier` 取默认。
        `images`：图片 data URL(vision)，透传给 complete 组装多模态消息。
        """
        # 给模型完整 JSON Schema（含嵌套 $defs），否则像 PlanningOutput 这种嵌套结构
        # 便宜模型产不出合法 JSON → 解析失败 → 调用方掉 stub。
        try:
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        except Exception:
            schema_json = json.dumps(_schema_fields(schema), ensure_ascii=False)
        schema_hint = (
            "\n\n只输出一个 JSON 对象，必须严格匹配下面的 JSON Schema"
            "（不要解释、不要 markdown 代码块外的内容）：\n"
            f"{schema_json}"
        )
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            result = self.complete(
                prompt + schema_hint,
                model_tier=model_tier,
                system=system,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                response_format={"type": "json_object"},
                model=model,
                images=images,
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
