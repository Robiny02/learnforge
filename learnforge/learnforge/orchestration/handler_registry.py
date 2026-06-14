"""能力 Handler 注册表（轻量 DDD 重构 Phase 2，纯加法、未接线）。

`HandlerRegistry`：`Capability → CapabilityHandler` 的分发表。目标是 Phase 3 让 Manager 把
`_dispatch_impl` 里硬编码的 `if agent=="qa"/"diagnosis"/...` 改成 `registry.get(cap).run(...)`，
新增能力=注册一个 handler，**不动主循环**。

本阶段只提供注册表的**结构与单元行为**，默认是空表，**尚未接入** ManagerAgent（行为零变化）。
设计文档见 `docs/architecture/refactor-plan.md`。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union

from ..contracts.enums import EvidenceSourceType  # noqa: F401  (供后续 provider 注册表复用)
from ..contracts.handlers import CapabilityHandler
from ..contracts.intent import Capability


class HandlerRegistry:
    """Capability → CapabilityHandler 的最小注册表（线程无关、进程内）。"""

    def __init__(self) -> None:
        self._handlers: Dict[Capability, CapabilityHandler] = {}

    @staticmethod
    def _coerce(capability: Union[Capability, str]) -> Capability:
        """容忍传入 str 或 Capability，统一规整为 Capability（未知值抛 ValueError）。"""
        if isinstance(capability, Capability):
            return capability
        return Capability(str(capability))

    def register(self, handler: CapabilityHandler) -> None:
        """登记一个能力 handler（按其 `capability` 入表，重复登记后者覆盖前者）。"""
        cap = self._coerce(getattr(handler, "capability"))
        self._handlers[cap] = handler

    def get(self, capability: Union[Capability, str]) -> Optional[CapabilityHandler]:
        """取某能力的 handler；未注册返回 None（调用方决定回退/报错）。"""
        try:
            return self._handlers.get(self._coerce(capability))
        except ValueError:
            return None

    def has(self, capability: Union[Capability, str]) -> bool:
        try:
            return self._coerce(capability) in self._handlers
        except ValueError:
            return False

    def capabilities(self) -> List[Capability]:
        return list(self._handlers.keys())

    def __len__(self) -> int:
        return len(self._handlers)

    def __contains__(self, capability: object) -> bool:
        if isinstance(capability, (Capability, str)):
            return self.has(capability)
        return False


# 进程内单例（Phase 2 默认空；Phase 3 在 ManagerAgent 构造时注册 QA/Diagnosis/Planning/Mock handler）。
HANDLER_REGISTRY = HandlerRegistry()
