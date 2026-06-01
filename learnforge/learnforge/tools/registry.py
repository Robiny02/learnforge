"""MCPRegistry —— 工具名 → ToolSpec 解析，并作为 skill.allowed_tools 的 canonical 清单。

skill.spec.allowed_tools 中的工具名经此查找/校验：注册表登记了系统当前真实使用的
内部能力名（LLM、检索、仓储读写、子 agent 派发、mock checkpoint）。这样 skill 声明的
工具就有了 single source of truth，typo（如 `retrieval.serch`）会在 skill 注册期被发现。

注意：这些是**内部能力**名，不是外部 MCP server 工具。外部 MCP 接入仍是 MVP Non-Goal
（见 base.py），但权限闸（Skill.allows / BaseAgent.require_tool）正是以这些名字为边界。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .spec import ToolEffect, ToolSpec


class MCPRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def resolve(self, names: List[str]) -> List[ToolSpec]:
        """解析 skill 的 allowed_tools；未注册的工具被忽略。"""
        return [self._tools[n] for n in names if n in self._tools]

    def is_known(self, name: str, for_declaration: bool = False) -> bool:
        """工具名是否在 canonical 清单内。

        默认用于查询：精确匹配，或命中显式 namespace 的子项。
        for_declaration=True 用于 skill 注册期：必须精确匹配可声明工具；
        只有 declarable=True 且 allow_prefix=True 的 namespace 才能被声明为前缀。
        """
        spec = self._tools.get(name)
        if spec is not None:
            if not for_declaration:
                return True
            return spec.declarable
        if for_declaration:
            return False
        return self._namespace_for(name) is not None

    def spec_for(self, name: str) -> Optional[ToolSpec]:
        """返回精确工具或匹配的 namespace spec。"""
        return self._tools.get(name) or self._namespace_for(name)

    def may_declare(self, name: str, agent_id: str) -> bool:
        """某 agent 是否可在 skill.allowed_tools 声明该工具。"""
        spec = self._tools.get(name)
        if spec is None:
            return False
        if not spec.declarable:
            return False
        if spec.owner_agents and agent_id not in spec.owner_agents:
            return False
        return True

    def _namespace_for(self, name: str) -> Optional[ToolSpec]:
        for registered, spec in self._tools.items():
            if spec.is_namespace and spec.allow_prefix and name.startswith(f"{registered}."):
                return spec
        return None

    def is_declarable_namespace(self, name: str) -> bool:
        spec = self._tools.get(name)
        return bool(spec and spec.is_namespace and spec.allow_prefix and spec.declarable)

    def all_tools(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def write_tools(self) -> List[ToolSpec]:
        return [spec for spec in self._tools.values() if spec.effect.value == ToolEffect.WRITE.value]


def _tool(
    name: str,
    desc: str,
    effect: ToolEffect,
    owners: Optional[List[str]] = None,
    audit: bool = False,
    timeout_ms: int = 0,
    cost: float = 0.0,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=desc,
        effect=effect,
        owner_agents=owners or [],
        audit_required=audit,
        timeout_ms=timeout_ms,
        cost_budget_usd=cost,
    )


def _namespace(name: str, desc: str, effect: ToolEffect) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=desc,
        effect=effect,
        is_namespace=True,
        allow_prefix=True,
        declarable=False,
    )


def _populate(registry: "MCPRegistry") -> None:
    specs = [
        _namespace("llm", "Namespace for LLM capabilities.", ToolEffect.COMPUTE),
        _namespace("agent", "Namespace for subagent delegation.", ToolEffect.DELEGATE),
        _namespace("repository.read", "Namespace for read-only repositories.", ToolEffect.READ),
        _namespace("repository.write", "High-risk write namespace; exact tools only.", ToolEffect.WRITE),
        _namespace("retrieval", "Namespace for retrieval capabilities.", ToolEffect.READ),
        _namespace("notion", "Namespace for Notion external publishing.", ToolEffect.EXTERNAL),
        _namespace("mcp", "Namespace for external MCP server tools.", ToolEffect.EXTERNAL),
        _namespace("mock", "Namespace for mock session runtime capabilities.", ToolEffect.WRITE),
        _tool("notion.sync", "Publish a learning plan as a beautiful Notion note.",
              ToolEffect.EXTERNAL, owners=["planning"], audit=True),
        _tool("mcp.notion.search_pages", "Search pages/databases visible to the Notion integration.",
              ToolEffect.EXTERNAL, owners=["qa", "planning", "diagnosis"], audit=True, timeout_ms=60000),
        _tool("mcp.notion.read_page", "Read a Notion page as markdown-like text.",
              ToolEffect.EXTERNAL, owners=["qa", "planning", "diagnosis"], audit=True, timeout_ms=60000),
        _tool("mcp.notion.create_learning_note", "Create a Notion page for a LearnForge learning note.",
              ToolEffect.EXTERNAL, owners=["planning"], audit=True, timeout_ms=60000),
        _tool("mcp.web.fetch_url", "Fetch a web URL and save a reusable markdown artifact.",
              ToolEffect.EXTERNAL, owners=["qa", "retrieval", "planning", "diagnosis"], audit=True,
              timeout_ms=60000),
        _tool("mcp.github.repo_summary", "Read GitHub repository metadata, languages, and README.",
              ToolEffect.EXTERNAL, owners=["qa", "retrieval", "planning", "diagnosis"], audit=True,
              timeout_ms=60000),
        _tool("mcp.github.list_tree", "List a GitHub repository tree.",
              ToolEffect.EXTERNAL, owners=["qa", "retrieval", "planning"], audit=True, timeout_ms=60000),
        _tool("mcp.github.read_file", "Read one text file from a GitHub repository.",
              ToolEffect.EXTERNAL, owners=["qa", "retrieval", "planning", "diagnosis"], audit=True,
              timeout_ms=60000),
        _tool("plan.read_candidates", "Read real candidate knowledge atoms by topics.",
              ToolEffect.READ, owners=["planning"]),
        _tool("report.generate", "Generate a structured Markdown report file under docs/.",
              ToolEffect.COMPUTE, owners=["planning", "diagnosis"]),
        _tool("llm.complete", "Free-form LLM completion (query rewrite / aggregation).",
              ToolEffect.COMPUTE, timeout_ms=10000, cost=0.05),
        _tool("llm.complete_structured", "Structured LLM call parsed into a Pydantic schema.",
              ToolEffect.COMPUTE, timeout_ms=20000, cost=0.10),
        _tool("retrieval.search", "Read-only retrieval over configured knowledge sources.",
              ToolEffect.READ, timeout_ms=1500),
        _tool("mock.checkpoint", "Persist/resume LangGraph mock session state.",
              ToolEffect.WRITE, owners=["mock"], audit=True),
        # 子 agent 派发（编排者/外壳用）。
        _tool("agent.qa", "Delegate to the QA subsystem.", ToolEffect.DELEGATE, owners=["manager"]),
        _tool("agent.planning", "Delegate to the planning agent.", ToolEffect.DELEGATE, owners=["manager"]),
        _tool("agent.diagnosis", "Delegate to the read-only diagnosis agent.", ToolEffect.DELEGATE,
              owners=["manager"]),
        _tool("agent.mock", "Delegate to the mock interview subsystem.", ToolEffect.DELEGATE,
              owners=["manager"]),
        _tool("agent.router", "Delegate to the QA router.", ToolEffect.DELEGATE, owners=["qa"]),
        _tool("agent.synthesizer", "Delegate to the QA synthesizer.", ToolEffect.DELEGATE, owners=["qa"]),
        _tool("agent.verifier", "Delegate to the QA verifier.", ToolEffect.DELEGATE, owners=["qa"]),
        _tool("agent.interviewer", "Delegate to the mock interviewer.", ToolEffect.DELEGATE,
              owners=["mock"]),
        _tool("agent.judge", "Delegate to the mock judge.", ToolEffect.DELEGATE, owners=["mock"]),
        _tool("agent.strategist", "Delegate to the mock strategist.", ToolEffect.DELEGATE,
              owners=["mock"]),
        _tool("agent.coach", "Delegate to the mock coach.", ToolEffect.DELEGATE, owners=["mock"]),
        # 仓储读（只读 agent 可用）。
        _tool("repository.read.profile", "Read user profile.", ToolEffect.READ),
        _tool("repository.read.atoms", "Read knowledge atoms and mastery.", ToolEffect.READ),
        _tool("repository.read.events", "Read interaction events.", ToolEffect.READ),
        _tool("diagnosis.search_events", "Read interaction events for diagnosis evidence.",
              ToolEffect.READ, owners=["diagnosis"], timeout_ms=1000),
        _tool("diagnosis.get_mastery_snapshot", "Read effective mastery for requested atoms.",
              ToolEffect.READ, owners=["diagnosis"], timeout_ms=1000),
        _tool("diagnosis.search_qa_history", "Search local QA history for diagnosis evidence.",
              ToolEffect.READ, owners=["diagnosis"], timeout_ms=1000),
        _tool("diagnosis.search_mock_turns", "Search local mock interview turns for diagnosis evidence.",
              ToolEffect.READ, owners=["diagnosis"], timeout_ms=1000),
        _tool("diagnosis.retrieve_knowledge", "Retrieve shared/local knowledge to ground diagnosis.",
              ToolEffect.READ, owners=["diagnosis"], timeout_ms=1500),
        _tool("diagnosis.analyze_code_static", "Run deterministic static code analysis for diagnosis.",
              ToolEffect.COMPUTE, owners=["diagnosis"], timeout_ms=1000),
        # 仓储写（仅授权 agent：Manager 全量、Mock 仅 append 本场记录）。
        _tool("repository.write.learning_path", "Commit learning paths (Manager only).",
              ToolEffect.WRITE, owners=["manager"], audit=True),
        _tool("repository.write.mastery", "Commit mastery scores (Manager only).",
              ToolEffect.WRITE, owners=["manager"], audit=True),
        _tool("repository.write.events", "Append interaction events.",
              ToolEffect.WRITE, owners=["manager", "mock"], audit=True),
        _tool("repository.write.traces", "Append agent traces.",
              ToolEffect.WRITE, owners=["manager"], audit=True),
        _tool("repository.write.diagnosis_reports", "Append diagnosis report snapshots (Manager only).",
              ToolEffect.WRITE, owners=["manager"], audit=True),
        _tool("repository.write.mock_sessions", "Append mock session/turn records (Mock only).",
              ToolEffect.WRITE, owners=["mock"], audit=True),
    ]
    for spec in specs:
        registry.register(spec)


# 全局单例（启动即登记 canonical 工具，供 skill 注册期校验）。
MCP_REGISTRY = MCPRegistry()
_populate(MCP_REGISTRY)
