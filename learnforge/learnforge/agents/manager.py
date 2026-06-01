"""兼容 shim：ManagerAgent 已迁至编排层 `learnforge.orchestration.manager`。

保留此模块仅为不破坏历史 import（tests / app / graph 等用 `from learnforge.agents.manager import ManagerAgent`）。
新代码请用 `from ..orchestration.manager import ManagerAgent`。
"""

from ..orchestration.manager import (  # noqa: F401
    NextStep,
    PlanDAG,
    PlanTask,
    ManagerAgent,
)
