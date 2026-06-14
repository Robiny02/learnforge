"""Phase 2 测试：Handler 协议 + HandlerRegistry（纯加法、未接线，零行为改动）。

只断言协议结构与注册表单元行为；不触碰 Manager / 子 Agent / 派发流程。
设计文档：docs/architecture/refactor-plan.md。
"""

from __future__ import annotations

from typing import Any, Dict, List

from learnforge.contracts import (
    AgentResult,
    CapabilityHandler,
    EvidenceProviderHandler,
    ToolHandler,
)
from learnforge.contracts.agents.evidence import EvidenceRef, ProviderResult
from learnforge.contracts.enums import EvidenceSourceType, Status
from learnforge.contracts.intent import Capability
from learnforge.orchestration.handler_registry import HANDLER_REGISTRY, HandlerRegistry


# --- 测试替身（满足协议的最小实现）-------------------------------------------
class _DummyCapability:
    capability = Capability.QA

    def run(self, request: Any, context: Dict[str, Any]) -> AgentResult:
        return AgentResult(status=Status.OK, confidence=1.0, payload={"echo": request})


class _DummyProvider:
    source_type = EvidenceSourceType.FILE

    def fetch(self, query: str, targets: List[str], context: Dict[str, Any]) -> ProviderResult:
        return ProviderResult(evidence_refs=[EvidenceRef(locator=t, snippet="x") for t in targets])


class _DummyTool:
    name = "demo.tool"
    effect = "read"

    def __call__(self, arguments: Dict[str, Any]):
        return {"ok": True, "args": arguments}


# --- 协议（runtime_checkable）-------------------------------------------------
def test_protocols_are_runtime_checkable():
    assert isinstance(_DummyCapability(), CapabilityHandler)
    assert isinstance(_DummyProvider(), EvidenceProviderHandler)
    assert isinstance(_DummyTool(), ToolHandler)


def test_non_conforming_object_is_not_a_capability_handler():
    class _Missing:  # 没有 run 方法
        capability = Capability.QA

    assert not isinstance(_Missing(), CapabilityHandler)


# --- 注册表 -------------------------------------------------------------------
def test_registry_starts_empty_and_registers():
    reg = HandlerRegistry()
    assert len(reg) == 0
    assert reg.get(Capability.QA) is None
    assert not reg.has("qa")

    h = _DummyCapability()
    reg.register(h)
    assert len(reg) == 1
    assert reg.get(Capability.QA) is h
    assert reg.has("qa")               # 接受 str
    assert Capability.QA in reg
    assert reg.capabilities() == [Capability.QA]


def test_registry_coerces_str_and_handles_unknown():
    reg = HandlerRegistry()
    reg.register(_DummyCapability())
    assert reg.get("qa") is not None    # str 规整为 Capability
    assert reg.get("not-a-capability") is None
    assert reg.has("not-a-capability") is False


def test_register_overwrites_same_capability():
    reg = HandlerRegistry()
    a, b = _DummyCapability(), _DummyCapability()
    reg.register(a)
    reg.register(b)
    assert len(reg) == 1
    assert reg.get(Capability.QA) is b


def test_global_singleton_is_unwired_in_phase2():
    """Phase 2 不接线：全局注册表默认空（Manager 尚未注册任何 handler）。"""
    assert isinstance(HANDLER_REGISTRY, HandlerRegistry)
    assert len(HANDLER_REGISTRY) == 0


def test_dummy_capability_run_returns_agent_result():
    out = _DummyCapability().run("hello", {})
    assert isinstance(out, AgentResult)
    assert out.status == Status.OK and out.payload == {"echo": "hello"}
