"""QA 子系统 input/output（Design §3.2-§3.5）。

QAAgent 外壳编排 Router → Retrieval → Synthesizer → Verifier。
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from ..atom import AtomBrief
from ..enums import QType, SourceType, Verdict
from .retrieval import Chunk


class Citation(BaseModel):
    chunk_id: str
    source_type: SourceType = SourceType.ATOM
    snippet: str = ""


class Claim(BaseModel):
    """可验证声明（Synthesizer 产出 → Verifier 校验）。"""

    claim_id: str
    text: str
    source_chunk_id: Optional[str] = None


# --- RouterAgent (§3.3) ---
class RouterInput(BaseModel):
    question: str
    recent_topics: List[str] = Field(default_factory=list)


class RouterOutput(BaseModel):
    q_type: QType
    need_retrieval: bool
    need_verifier: bool
    rewrite_needed: bool = False


# --- SynthesizerAgent (§3.4) ---
class SynthesizerInput(BaseModel):
    question: str
    retrieved: List[Chunk] = Field(default_factory=list)
    scoped_atoms: List[AtomBrief] = Field(default_factory=list)
    project_context: Optional[str] = None


class SynthesizerOutput(BaseModel):
    draft: str
    claims: List[Claim] = Field(default_factory=list)


# --- VerifierAgent (§3.5) ---
class FlaggedClaim(BaseModel):
    claim_id: str
    reason: str


class VerifierInput(BaseModel):
    draft: str
    claims: List[Claim] = Field(default_factory=list)
    retrieved: List[Chunk] = Field(default_factory=list)


class VerifierOutput(BaseModel):
    verified_answer: str
    verdict: Verdict
    flagged: List[FlaggedClaim] = Field(default_factory=list)


# --- QAAgent 外壳 (§3.2) ---
class QAInput(BaseModel):
    question: str
    project_context_ref: Optional[str] = None
    scoped_atoms: List[AtomBrief] = Field(default_factory=list)


class QAOutput(BaseModel):
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    verdict: Verdict = Verdict.UNCERTAIN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    topic: Optional[str] = Field(default=None, description="信号归属话题（取检索片段 topic 或 q_type）。")
