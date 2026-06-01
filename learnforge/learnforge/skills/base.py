"""Skill 抽象（预留）——agent 工作流的可控单元。

设计动机（用户要求 + Design §6c）：
很多 agent 的工作流应由 **skill** 驱动，而非把 prompt / 模型档位 / 可用工具 / 步骤
硬编码进 agent 实现。一个 Skill 把以下东西打包成一个可注册、可替换的能力单元：

  - system_prompt：该 agent 的身份/边界/反模式（Design §3 各节"System Prompt 大纲"）
  - model_tier：Haiku / Sonnet（Design §3 各节 Bounds）
  - allowed_tools：可调用的 MCP/本地工具名（也是运行期权限边界）
  - input/output schema：绑定 contracts 里的 Pydantic 模型，保证结构化输出
  - steps：可选的工作流步骤声明（供 agent 编排，如 QA 的 route→retrieve→synth→verify）

Phase 1：仅定义协议与数据结构，run() 为 stub。Phase 2/3 再填实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

from ..contracts.enums import AgentId, ModelTier


class SkillPermissionError(PermissionError):
    """Skill 尝试使用未授权工具时抛出。"""


@dataclass
class SkillSpec:
    """声明式 skill 定义（一个 agent 可绑定一个或多个 skill）。"""

    name: str
    agent_id: AgentId
    model_tier: ModelTier
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    allowed_tools: List[str] = field(default_factory=list)  # MCP/工具名，经 mcp.registry 解析
    tool_descriptions: Dict[str, str] = field(default_factory=dict)
    subskill_descriptions: Dict[str, str] = field(default_factory=dict)
    bash: List[str] = field(default_factory=list)
    workflow: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    triggers: Dict[str, List[str]] = field(default_factory=dict)
    scripts: Dict[str, str] = field(default_factory=dict)
    max_react_steps: int = 0
    progressive_sections: Dict[str, str] = field(default_factory=dict)
    strategy_rules: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    input_schema: Optional[Type[BaseModel]] = None
    output_schema: Optional[Type[BaseModel]] = None
    steps: List[str] = field(default_factory=list)  # 工作流步骤声明（可选）
    metadata: Dict[str, Any] = field(default_factory=dict)


class Skill:
    """运行期 skill 句柄。Phase 1 为 stub：不调用 LLM，直接回显占位。

    真实现（Phase 2+）将在此：组装 prompt（经 memory.context_assembler）、
    解析 allowed_tools（经 mcp.registry）、调用 llm.client、校验 output_schema。
    """

    def __init__(self, spec: SkillSpec, handler: Optional[Callable[[BaseModel], BaseModel]] = None):
        self.spec = spec
        self._handler = handler

    def run(self, payload: BaseModel) -> BaseModel:
        """执行 skill。Phase 1：若提供 handler 则调用，否则抛未实现。"""
        if self._handler is not None:
            return self._handler(payload)
        raise NotImplementedError(
            f"Skill '{self.spec.name}' ({self.spec.agent_id}) 尚未实现（Phase 1 stub）。"
        )

    def allows(self, tool_name: str) -> bool:
        """是否允许使用某个工具/能力。

        工具名支持前缀授权：声明 `repository.read` 会允许
        `repository.read.atoms` / `repository.read.events`。
        """
        return any(tool_name == t or tool_name.startswith(f"{t}.") for t in self.spec.allowed_tools)

    def workflow(self) -> List[str]:
        """兼容旧 steps，同时优先使用更完整的 workflow 声明。"""
        return self.spec.workflow or self.spec.steps

    def strategy_for(self, key: str, default: Optional[str] = None) -> Dict[str, Any]:
        """按运行期信号选择策略。

        例如 QA shell 会用 q_type 选择 concept/project/chitchat 策略；如果没有命中，
        则回退到 default，再回退到空 dict。
        """
        if key in self.spec.strategy_rules:
            return dict(self.spec.strategy_rules[key])
        if default and default in self.spec.strategy_rules:
            return dict(self.spec.strategy_rules[default])
        return {}

    def required_tools_for_strategy(self, key: str, default: Optional[str] = None) -> List[str]:
        """返回某策略声明需要的工具，供 agent 在执行前统一校验。"""
        return list(self.strategy_for(key, default=default).get("tools", []))

    def load_instructions(self, sections: Optional[List[str]] = None) -> str:
        """渐进式加载 skill 说明。

        默认只加载 front/constraints/workflow 这些高信号、稳定的边界信息；
        调用方需要工具细节、子 skill 细节、bash 能力或扩展材料时再按 section 名称加载。
        """
        selected = sections or ["front", "constraints", "workflow"]
        chunks: List[str] = []
        for section in selected:
            rendered = self._render_section(section)
            if rendered:
                chunks.append(rendered)
        return "\n\n".join(chunks)

    def _render_section(self, section: str) -> str:
        if section == "front" and self.spec.frontmatter:
            items = [f"{k}: {v}" for k, v in self.spec.frontmatter.items()]
            return "Front Matter:\n" + "\n".join(f"- {x}" for x in items)
        if section == "tools" and self.spec.tool_descriptions:
            return "Tools:\n" + "\n".join(
                f"- {name}: {desc}" for name, desc in self.spec.tool_descriptions.items()
            )
        if section == "subskills" and self.spec.subskill_descriptions:
            return "Subskills:\n" + "\n".join(
                f"- {name}: {desc}" for name, desc in self.spec.subskill_descriptions.items()
            )
        if section == "bash" and self.spec.bash:
            return "Bash:\n" + "\n".join(f"- {cmd}" for cmd in self.spec.bash)
        if section == "workflow" and self.workflow():
            return "Workflow:\n" + "\n".join(f"- {step}" for step in self.workflow())
        if section == "constraints" and self.spec.constraints:
            return "Constraints:\n" + "\n".join(f"- {c}" for c in self.spec.constraints)
        if section == "triggers" and self.spec.triggers:
            lines = []
            for key, values in self.spec.triggers.items():
                lines.append(f"- {key}: {', '.join(values)}")
            return "Triggers:\n" + "\n".join(lines)
        if section == "scripts" and self.spec.scripts:
            return "Scripts:\n" + "\n".join(
                f"- {name}: {path}" for name, path in self.spec.scripts.items()
            )
        return self.spec.progressive_sections.get(section, "")
