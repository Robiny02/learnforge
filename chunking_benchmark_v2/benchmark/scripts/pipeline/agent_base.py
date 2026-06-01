"""Base class for pipeline agents (mirrors learnforge agents/base.py).

Each agent declares a `name`, runs within a permission-style capability list, and
shares a single LLM access point that degrades to None when no key is configured
— so every agent has a deterministic fallback and the chain never breaks.
"""

from __future__ import annotations

import os
from typing import List, Optional

# Chat model used for synthesis/classification recheck (OpenRouter-style id).
LLM_MODEL = os.getenv("PIPELINE_LLM_MODEL", "openai/gpt-4o-mini")


class LLMGateway:
    """Thin shared LLM client. available=False when no key/SDK → callers fall back."""

    def __init__(self) -> None:
        self.model = LLM_MODEL
        self._client = None
        self.available = False
        key = os.getenv("OPENAI_API_KEY")
        if key:
            try:
                import openai  # type: ignore

                base = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
                self._client = openai.OpenAI(api_key=key, base_url=base)
                self.available = True
            except Exception:
                self.available = False

    def chat(self, system: str, user: str, max_tokens: int = 700) -> Optional[str]:
        if not self.available or self._client is None:
            return None
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return None


# Process-wide singleton (like learnforge LLM = LLMClient()).
LLM = LLMGateway()


class PipelineAgent:
    name: str = "agent"
    capabilities: List[str] = []

    def __init__(self, llm: Optional[LLMGateway] = None) -> None:
        self.llm = llm or LLM

    def require(self, capability: str) -> None:
        """Soft permission gate (mirrors learnforge require_tool); declared, not enforced
        against an external registry here, but keeps the boundary explicit."""
        if self.capabilities and capability not in self.capabilities:
            raise PermissionError(f"agent '{self.name}' is not allowed to use '{capability}'")
