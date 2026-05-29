"""DiagnosisAgent input/output（Design §3.13 / §5.5）。

严格只读：聚合 Atom 掌握度 + interaction_events，产出弱点结论与行动建议，不写任何 state。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from ..enums import DiagnosisTrigger, TimeWindow


class WeakAtom(BaseModel):
    atom_id: str
    topic: str
    mastery: float = Field(ge=0.0, le=1.0)
    evidence_count: int = 0


class Cluster(BaseModel):
    topic: str
    severity: float = Field(ge=0.0, le=1.0, description="弱点簇严重度。")


class DiagnosisInput(BaseModel):
    time_window: TimeWindow = TimeWindow.D30
    focus_topics: List[str] = Field(default_factory=list)
    trigger: DiagnosisTrigger = DiagnosisTrigger.USER


class DiagnosisResult(BaseModel):
    """诊断结论（只读输出；写操作必经 Manager→Planning，Proposal §5 决策 5）。"""

    weak_atoms: List[WeakAtom] = Field(default_factory=list)
    clusters: List[Cluster] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
