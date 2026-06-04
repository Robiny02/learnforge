"""Planning 工具：生成/修改学习路径（PlanningAgent，只产增量 PathDiff）。

公开入口保持兼容：`from learnforge.agents.planning import PlanningAgent`。
工具契约见 ../../contracts/agents/planning.py；运行时人格见 skills 注册表；本包文档见 CLAUDE.md。
"""

from .planning_agent import PlanningAgent

__all__ = ["PlanningAgent"]
