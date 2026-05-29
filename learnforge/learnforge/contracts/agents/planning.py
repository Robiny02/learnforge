"""PlanningAgent input/output（Design §3.7，生成+修改合并）。

产出 PathDiff（增量，不全量重写）；由 Manager 单点落库（PlanningAgent 不直接写）。
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

from ..enums import PlanMode, Status
from .diagnosis import DiagnosisResult


class PathItem(BaseModel):
    """学习路径条目（对应 path_items 表，Design §4c）。"""

    item_id: str
    atom_id: str
    order_idx: int
    day_index: int
    status: str = "pending"


class ReorderOp(BaseModel):
    item_id: str
    new_order: int


class PathDiff(BaseModel):
    """增量 diff（Design §3.7 输出，不全量重写）。"""

    add: List[PathItem] = Field(default_factory=list)
    remove: List[str] = Field(default_factory=list, description="待删除的 item_id 列表。")
    reorder: List[ReorderOp] = Field(default_factory=list)
    rationale: str = ""


class PlanningInput(BaseModel):
    """两分支共用（Design §3.7）：generate 需 goal；modify 需 diagnosis + existing_path_ref。"""

    mode: PlanMode
    goal: Optional[str] = None  # required if mode == generate
    deadline: Optional[date] = None
    current_level: Optional[str] = None
    diagnosis: Optional[DiagnosisResult] = None  # required if mode == modify
    existing_path_ref: Optional[str] = None  # required if mode == modify
    user_feedback: Optional[str] = None


class PlanningOutput(BaseModel):
    diff: PathDiff = Field(default_factory=PathDiff)
    status: Status = Status.OK
