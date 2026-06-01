"""ToolCollection —— 单一工具集合（合并原来两套 handler 表）。

历史上有两套：runtime.DEFAULT_TOOL_REGISTRY(返回 ToolResult，程序化调用) 与
openai_bridge.TOOL_RUNTIME(返回 dict，LLM tool-calling)。现在**统一到一个底层 store**：
`TOOLS`。两条调用面只是它的两种视图：
- call_result(name,args) -> ToolResult（程序化，ToolRuntime/call_tool 用）
- call_dict(name,args)   -> dict（LLM 回灌，ReactRunner 用）

handler 可返回 ToolResult 或 dict，均自动归一化；handler=None = 仅声明 schema（真执行由
ReactRunner 的 per-call handler 提供）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import ToolResult

_SEP = "__"  # OpenAI 函数名不能含 '.'，转义


def safe_name(name: str) -> str:
    return name.replace(".", _SEP)


def unsafe_name(name: str) -> str:
    return name.replace(_SEP, ".")


@dataclass
class ToolCollection:
    handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = field(default_factory=dict)
    params: Dict[str, dict] = field(default_factory=dict)
    descs: Dict[str, str] = field(default_factory=dict)

    def register(self, name: str, handler: Optional[Callable] = None,
                 parameters: Optional[dict] = None, description: str = "") -> None:
        if handler is not None:
            self.handlers[name] = handler
        self.params[name] = parameters or self.params.get(name) or {"type": "object", "properties": {}}
        if description:
            self.descs[name] = description

    def has(self, name: str) -> bool:
        return name in self.handlers or name in self.params

    def has_handler(self, name: str) -> bool:
        return name in self.handlers

    def names(self) -> List[str]:
        return list({*self.handlers, *self.params})

    # --- 两种执行视图（同一 store）---
    def call_result(self, name: str, args: Optional[dict] = None) -> ToolResult:
        h = self.handlers.get(name)
        if h is None:
            return ToolResult(name=name, ok=False, observation=f"no handler for {name}",
                              error="unknown_tool")
        try:
            out = h(args or {})
        except Exception as e:  # noqa: BLE001
            return ToolResult(name=name, ok=False, observation=str(e),
                              error=f"{type(e).__name__}: {e}")
        if isinstance(out, ToolResult):
            return out
        if isinstance(out, dict):
            err = out.get("error")
            return ToolResult(name=name, ok=not err, observation=out.get("observation", ""),
                              data=out, error=err)
        return ToolResult(name=name, ok=True, observation="", data=out)

    def call_dict(self, name: str, args: Optional[dict] = None) -> Dict[str, Any]:
        h = self.handlers.get(name)
        if h is None:
            return {"error": f"no handler registered for tool '{name}'"}
        try:
            out = h(args or {})
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
        if isinstance(out, ToolResult):
            d: Dict[str, Any] = {"ok": out.ok, "observation": out.observation}
            if isinstance(out.data, dict):
                d.update(out.data)
            elif out.data is not None:
                d["data"] = out.data
            if out.error:
                d["error"] = out.error
            return d
        if isinstance(out, dict):
            return out
        return {"result": out}

    def to_openai_tools(self, names: List[str]) -> List[dict]:
        from .registry import MCP_REGISTRY

        out: List[dict] = []
        for name in names:
            if name not in self.handlers and name not in self.params:
                continue
            spec = MCP_REGISTRY.spec_for(name)
            desc = self.descs.get(name) or (spec.description if spec else "") or name
            out.append({"type": "function", "function": {
                "name": safe_name(name), "description": desc,
                "parameters": self.params.get(name) or {"type": "object", "properties": {}}}})
        return out


# 全局单一工具集合。
TOOLS = ToolCollection()
