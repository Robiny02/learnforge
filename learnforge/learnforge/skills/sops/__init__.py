"""各 Agent 的结构化 SOP（按能力域分文件，便于独立 review/编辑）。

一个文件 = 一个能力域，方便"只看 QA SOP 就理解 QA 该怎么做事"：
  - qa.py        —— QA 答题(Synthesizer) + QA 外壳编排
  - planning.py  —— 学习路径规划
  - diagnosis.py —— 弱点诊断 + 简历诊断
  - mock.py      —— mock 外壳 + 面试官 + 教练
  - manager.py   —— 主编排器（SOP 已定义，但 Manager 仍绕过 assembler，注入待 Step F/B）

`SOPS` 按 **skill name** 索引（一个 agent_id 可能有多个 skill，如 diagnosis 弱点/简历）。
未在此登记的 skill（Router/Verifier/Retrieval/Judge/Strategist）暂回退 system_prompt，
后续按需迁移（见 README.md）。

维护：改某个 Agent 的 SOP 只动对应文件里的常量；不需要改 prompt assembler 主链路。
改完跑 `pytest tests/test_sop.py` + 该 Agent 自己的测试。
"""

from __future__ import annotations

from typing import Dict

from ..sop import SOP
from .diagnosis import DIAGNOSIS_SOP, RESUME_DIAGNOSIS_SOP
from .manager import MANAGER_SOP
from .mock import COACH_SOP, INTERVIEWER_SOP, MOCK_SHELL_SOP
from .planning import PLANNING_SOP
from .qa import QA_SHELL_SOP, SYNTHESIZER_SOP

# 按 skill name 索引（与 SkillSpec.name 对齐），供测试与登记使用。
SOPS: Dict[str, SOP] = {
    "manager.v1": MANAGER_SOP,
    "qa.shell.v1": QA_SHELL_SOP,
    "qa.synthesizer.v1": SYNTHESIZER_SOP,
    "planning.v1": PLANNING_SOP,
    "diagnosis.v1": DIAGNOSIS_SOP,
    "diagnosis.resume.v1": RESUME_DIAGNOSIS_SOP,
    "mock.shell.v1": MOCK_SHELL_SOP,
    "mock.interviewer.v1": INTERVIEWER_SOP,
    "mock.coach.v1": COACH_SOP,
}

__all__ = [
    "SOPS",
    "MANAGER_SOP",
    "QA_SHELL_SOP",
    "SYNTHESIZER_SOP",
    "PLANNING_SOP",
    "DIAGNOSIS_SOP",
    "RESUME_DIAGNOSIS_SOP",
    "MOCK_SHELL_SOP",
    "INTERVIEWER_SOP",
    "COACH_SOP",
]
