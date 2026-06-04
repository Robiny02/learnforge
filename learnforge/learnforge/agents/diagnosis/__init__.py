"""Diagnosis 工具：只读弱点诊断（DiagnosisAgent，ReAct 三段式）。

公开入口保持兼容：`from learnforge.agents.diagnosis import DiagnosisAgent`。
工具契约见 ../../contracts/agents/diagnosis.py；运行时人格见 skills 注册表；本包文档见 CLAUDE.md。
"""

from .diagnosis_agent import DiagnosisAgent, _is_weak_signal  # _is_weak_signal 供 eval 复用

__all__ = ["DiagnosisAgent", "_is_weak_signal"]
