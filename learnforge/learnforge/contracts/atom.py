"""Knowledge Atom —— 最小学习单位与掌握度状态（Design §4d，Proposal §1 闭环载体）。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from .enums import MasteryTier


class KnowledgeAtom(BaseModel):
    """主表 knowledge_atoms 的行模型（Design §4d，字段需与 schema.sql 完全一致）。"""

    atom_id: str = Field(..., description="主键，str(uuid4)。")
    topic: str = Field(..., description="如 'concurrency'。")
    subtopic: Optional[str] = None
    title: str
    canonical_question: str
    canonical_answer: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    difficulty: int = Field(default=3, ge=1, le=5)

    # 掌握度（Q1：连续分 + 离散分档 + 时间遗忘）
    mastery_score: float = Field(default=0.0, ge=0.0, le=1.0)
    mastery_tier: MasteryTier = MasteryTier.UNKNOWN
    last_reviewed_at: Optional[datetime] = None
    review_count: int = 0
    correct_count: int = 0
    decay_rate: float = Field(default=0.05, description="时间遗忘速率（Design §4d 掌握度算法）。")

    source_refs: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AtomBrief(BaseModel):
    """轻量作用域切片（Proposal §5 决策 8：只给完成任务所需的最小切片）。"""

    atom_id: str
    topic: str
    title: str
    mastery_score: float = 0.0
    mastery_tier: MasteryTier = MasteryTier.UNKNOWN
