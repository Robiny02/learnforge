"""Agent 基类（Phase 2：skill-driven 调用 + 优雅降级）。

每个 agent 从 SkillRegistry 取自己的 skill（用户要求：工作流由 skill 控制）。
`llm_structured()` 用 skill 的 system_prompt + model_tier 调 LLM 并解析为指定 schema；
无 key / 解析失败时返回 None，由各 agent 回退到自己的 stub，保证链路永远通。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ConfigDict

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


class RecallResult(BaseModel):
    """统一检索结果：召回片段 + 可直接注入 `retrieved` 槽的证据文本。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    chunks: List[object] = []
    text: str = ""
    degraded: bool = False


def _format_evidence(chunks: List[object]) -> str:
    """把召回片段渲染成带来源的证据块（进 assembler `retrieved` 槽，不污染 user_input）。"""
    lines: List[str] = []
    for c in chunks:
        meta = getattr(c, "metadata", None) or {}
        src = meta.get("filename") if isinstance(meta, dict) else None
        tag = src or getattr(c, "chunk_id", "")
        text = (getattr(c, "text", "") or "").strip().replace("\n", " ")
        if text:
            lines.append(f"- [{tag}] {text[:320]}")
    return ("【召回材料】\n" + "\n".join(lines)) if lines else ""


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

    def llm_structured(
        self,
        prompt: str,
        schema: Type[T],
        max_tokens: int = 1024,
        *,
        retrieved: str = "",
        session: str = "",
        images: Optional[List[str]] = None,
    ) -> Optional[T]:
        """skill-driven 结构化调用；不可用/失败返回 None（调用方回退 stub）。

        Claude-like agent 边界：每次 LLM 调用都携带该 subagent 的 skill prompt、
        工具权限清单、输入/输出契约与步骤偏好；是否能调用 LLM 也由 skill 权限决定。

        `retrieved`/`session`：检索证据 / 会话(handoff·项目)上下文。经 assembler 的有序槽位
        注入到 **volatile 尾部**（稳定 prefix 之后 → 不破坏缓存边界，§6c），调用方有就传，
        别再各自往 `prompt` 里手工拼接（消除 QA/mock 各写一套的漂移）。
        """
        if self.skill is None or not LLM.available:
            return None
        self.require_tool("llm.complete_structured")
        assembled = assemble_prompt(
            skill=self.skill,
            constitution=self._skill_constitution(schema),
            memory_summary=MEMORY.memory_prefix(db_path=getattr(self, "_db_path", None)),
            retrieved=retrieved,
            handoff_summary=session,
            user_input=prompt,
        )
        try:
            obj, result = LLM.complete_structured(
                prompt=assembled.volatile_suffix(),
                schema=schema,
                model_tier=self.skill.spec.model_tier,
                system=assembled.cacheable_prefix(),
                max_tokens=max_tokens,
                images=images,
            )
            self.last_cost_usd = result.cost_usd
            self.last_tokens = result.tokens
            return obj
        except (LLMUnavailable, LLMStructuredError):
            return None

    # ------------------------------------------------------------- 统一检索接口
    def recall(
        self,
        query: str,
        *,
        scopes: Optional[List[object]] = None,
        method: Optional[object] = None,
        filters: Optional[object] = None,
        top_k: int = 6,
        document_id: Optional[str] = None,
        origin: Optional[str] = None,
    ) -> "RecallResult":
        """所有专家 agent 共享的按需检索入口（统一封装 RetrievalAgent，不各自拼 SQL）。

        约定：返回的 `.text` 已渲染成证据块，调用方把它喂给 `llm_structured(retrieved=...)`，
        **不要**手工拼进 user_input。某 agent 不需要检索就别调用——这是懒触发、非强制。
        失败/降级一律返回空结果（"链路永远通"）。
        """
        from ..contracts.agents.retrieval import RetrievalFilters, RetrievalInput
        from ..contracts.enums import RetrievalMethod

        q = (query or "").strip()
        if not q and not document_id:
            return RecallResult()
        if filters is None and (document_id or origin):
            filters = RetrievalFilters(document_id=document_id, origin=origin)
        try:
            out = self._retriever().run(
                RetrievalInput(
                    query=q or (document_id or ""),
                    scopes=scopes,  # type: ignore[arg-type]
                    method=method or RetrievalMethod.FULLTEXT,  # type: ignore[arg-type]
                    filters=filters,  # type: ignore[arg-type]
                    top_k=top_k,
                )
            )
        except Exception:  # noqa: BLE001 - 检索失败不阻断对话
            return RecallResult(degraded=True)
        return RecallResult(chunks=list(out.chunks), text=_format_evidence(out.chunks),
                            degraded=out.degraded)

    def _retriever(self):
        """recall 专用的惰性 RetrievalAgent（与 agent 自带的 `self.retrieval` 隔离，
        避免把按需材料检索混进各 agent 既有的检索调用计数/缓存）。"""
        cached = getattr(self, "_recall_agent", None)
        if cached is None:
            from .retrieval import RetrievalAgent

            cached = RetrievalAgent(db_path=getattr(self, "_db_path", None))
            self._recall_agent = cached
        return cached

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
