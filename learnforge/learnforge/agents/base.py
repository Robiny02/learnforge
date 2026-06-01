"""Agent 基类（Phase 2：skill-driven 调用 + 优雅降级）。

每个 agent 从 SkillRegistry 取自己的 skill（用户要求：工作流由 skill 控制）。
`llm_structured()` 用 skill 的 system_prompt + model_tier 调 LLM 并解析为指定 schema；
无 key / 解析失败时返回 None，由各 agent 回退到自己的 stub，保证链路永远通。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

from ..contracts.enums import AgentId
from ..contracts.message import TokenUsage
from ..llm.client import LLM, LLMStructuredError, LLMUnavailable
from ..memory.base import MEMORY
from ..memory.context_assembler import assemble_prompt
from ..mcp.registry import MCP_REGISTRY
from ..skills.base import Skill, SkillPermissionError
from ..skills.bootstrap import ensure_skills_registered
from ..skills.registry import SKILL_REGISTRY
from ..tools import ToolResult, ToolRuntime

T = TypeVar("T", bound=BaseModel)


class BaseAgent:
    agent_id: AgentId

    def __init__(self, agent_id: Optional[AgentId] = None) -> None:
        if agent_id is not None:
            self.agent_id = agent_id
        ensure_skills_registered()
        self.skill: Optional[Skill] = SKILL_REGISTRY.primary(self.agent_id)
        self.last_cost_usd: float = 0.0
        self.last_tokens: TokenUsage = TokenUsage()
        self.permission_audit: List[Dict[str, object]] = []
        self.tool_runtime = ToolRuntime(self)

    def llm_structured(self, prompt: str, schema: Type[T], max_tokens: int = 1024) -> Optional[T]:
        """skill-driven 结构化调用；不可用/失败返回 None（调用方回退 stub）。

        Claude-like agent 边界：每次 LLM 调用都携带该 subagent 的 skill prompt、
        工具权限清单、输入/输出契约与步骤偏好；是否能调用 LLM 也由 skill 权限决定。
        """
        if self.skill is None or not LLM.available:
            return None
        self.require_tool("llm.complete_structured")
        assembled = assemble_prompt(
            skill=self.skill,
            constitution=self._skill_constitution(schema),
            memory_summary=MEMORY.memory_prefix(db_path=getattr(self, "_db_path", None)),
            user_input=prompt,
        )
        try:
            obj, result = LLM.complete_structured(
                prompt=assembled.volatile_suffix(),
                schema=schema,
                model_tier=self.skill.spec.model_tier,
                system=assembled.cacheable_prefix(),
                max_tokens=max_tokens,
            )
            self.last_cost_usd = result.cost_usd
            self.last_tokens = result.tokens
            return obj
        except (LLMUnavailable, LLMStructuredError):
            return None

    def has_tool(self, tool_name: str) -> bool:
        """当前 agent 的 primary skill 是否允许使用工具/能力。"""
        if self.skill is None:
            return False
        return self.skill.allows(tool_name)

    def require_tool(self, tool_name: str) -> None:
        """运行期权限门禁：subagent 未授权则阻断。

        这让 agent 更像 Claude/Claude Code 的 subagent：职责、上下文和工具
        都由 skill 声明，而不是由外层 workflow 默许。
        """
        allowed = self.has_tool(tool_name)
        self._audit_tool(tool_name, allowed)
        if not allowed:
            skill_name = self.skill.spec.name if self.skill else "<none>"
            raise SkillPermissionError(
                f"Agent '{self.agent_id.value}' skill '{skill_name}' is not allowed to use '{tool_name}'."
            )

    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, object]] = None) -> ToolResult:
        """Execute a registered tool through the skill permission boundary."""
        return self.tool_runtime.call(tool_name, dict(arguments or {}))

    def _audit_tool(self, tool_name: str, allowed: bool) -> None:
        spec = MCP_REGISTRY.spec_for(tool_name)
        self.permission_audit.append(
            {
                "agent_id": self.agent_id.value,
                "skill": self.skill.spec.name if self.skill else None,
                "tool": tool_name,
                "allowed": allowed,
                "effect": spec.effect.value if spec else "unknown",
                "audit_required": spec.audit_required if spec else False,
            }
        )

    def _skill_constitution(self, schema: Type[BaseModel]) -> str:
        tool_list = ", ".join(self.skill.spec.allowed_tools) if self.skill else ""
        steps = " -> ".join(self.skill.workflow()) if self.skill and self.skill.workflow() else "autonomous"
        output_name = getattr(schema, "__name__", str(schema))
        skill_brief = self.skill.load_instructions() if self.skill else ""
        return (
            "Agent boundary:\n"
            f"- agent_id: {self.agent_id.value}\n"
            f"- allowed_tools: {tool_list or 'none'}\n"
            f"- operating_mode: decide within this boundary; escalate instead of reaching for unlisted tools\n"
            f"- preferred_steps: {steps}\n"
            f"- output_schema: {output_name}\n\n"
            f"{skill_brief}"
        )
