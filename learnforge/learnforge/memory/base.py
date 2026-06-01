"""MemoryStore —— 记忆装配器（不是长期存储子系统）。

简化后只承担两件事（Design §6b/§6c）：

  - memory_prefix(): 渲染"稳定规则层"——UserProfile 的目标/水平/偏好，作为可缓存 prefix
    （即设计里 Memory 段；人工可读的少量稳定偏好，不放历史、不放向量索引）。
  - summarize(): 唯一的 ≤512tok 摘要器（Haiku，缺 key 回退结构化 JSON）。跨 agent 的
    handoff_summary 与跨轮 conversation_summary 共用它，不再各写一套。

长期记忆不经此类：知识内容/索引直接走仓储 + 向量库（chunks/chunk_vectors），
检索统一经 RetrievalAgent。这样"长期记忆"就是向量库本身，而非又一层抽象。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..config import HANDOFF_SUMMARY_MAX_TOKENS
from ..contracts.enums import ModelTier


class MemoryStore:
    def memory_prefix(self, user_id: str = "default", db_path: Optional[str] = None) -> str:
        """稳定规则层 = MEMORY.md（OpenClaw 风格，整段加载、不索引、不衰减）。

        单一真值来源（REQUIREMENTS R3.5）：用户长期偏好以 MEMORY.md 为权威；UserProfile 仅作
        离线回退/展示，不作为偏好并行写入点。MEMORY.md 缺失/读失败时才回退渲染 UserProfile
        （再不行 → 空串），保证链路永远通。
        """
        try:
            from .files import read_root_memory

            text = read_root_memory()
            if text:
                return text
        except Exception:
            pass
        return self._profile_prefix(user_id, db_path)

    @staticmethod
    def _profile_prefix(user_id: str, db_path: Optional[str]) -> str:
        """回退：把 UserProfile 偏好渲染成紧凑 prefix；无档案/失败 → 空串。"""
        try:
            from ..storage.repositories import UserProfileRepository

            profile = UserProfileRepository(db_path=db_path).get(user_id)
        except Exception:
            return ""
        if profile is None:
            return ""
        parts = []
        if profile.goals:
            parts.append("学习目标：" + "；".join(profile.goals))
        if profile.current_level:
            parts.append(f"当前水平：{profile.current_level}")
        if profile.preferences:
            kv = "；".join(f"{k}={v}" for k, v in profile.preferences.items())
            parts.append(f"偏好：{kv}")
        if not parts:
            return ""
        return "【用户稳定偏好】\n" + "\n".join(parts)

    def summarize(
        self, payload: Dict[str, Any], max_tokens: int = HANDOFF_SUMMARY_MAX_TOKENS
    ) -> str:
        """把 {intent,key_facts,constraints,open_items} 压成 ≤max_tokens 摘要。

        Haiku 可用 → 调模型；否则回退结构化 JSON（"链路永远通"）。
        """
        normalized = {
            "intent": payload.get("intent", ""),
            "key_facts": payload.get("key_facts") or [],
            "constraints": payload.get("constraints") or [],
            "open_items": payload.get("open_items") or [],
        }
        try:
            from ..llm.client import LLM

            if LLM.available:
                res = LLM.complete(
                    prompt=f"把以下交接信息压缩成 ≤{max_tokens}token 的 JSON 摘要"
                    "{intent,key_facts,constraints,open_items}：\n"
                    + json.dumps(normalized, ensure_ascii=False),
                    model_tier=ModelTier.HAIKU,
                    max_tokens=max_tokens,
                )
                if res.text and res.text.strip():
                    return res.text.strip()
        except Exception:
            pass
        # 离线兜底：拼成可读短文本（不再 json.dumps，避免被重复喂入时递归套娃），
        # 并按 max_tokens 粗略截断防膨胀（REQUIREMENTS 测试 §6.5/§14-7）。
        bits = []
        if normalized["intent"]:
            bits.append(str(normalized["intent"]))
        bits += [str(f) for f in normalized["key_facts"] if f]
        bits += [f"约束：{c}" for c in normalized["constraints"] if c]
        bits += [f"待办：{o}" for o in normalized["open_items"] if o]
        return "；".join(bits)[: max_tokens * 2]


# 全局单例（装配器，无状态）。
MEMORY = MemoryStore()
