"""全局配置：模型分级 + 各 agent Bounds（Design §3 / Proposal §2 成本目标）。

Phase 1 仅作为常量与查表，不接入真实 client。
"""

from __future__ import annotations

from typing import Dict

from .contracts.enums import AgentId, ModelTier

# 数据库
DB_PATH = "learnforge.db"
EMBEDDING_DIM = 1024  # Design §7a（1024 vs 1536 待 spike 锁定，DG1）

# 全局编排上限（Design §3.1 Bounds）
MAX_REPLAN = 2
PLAN_TIMEOUT_S = 10.0
TOTAL_WORKFLOW_TIMEOUT_S = 60.0
MAX_COST_PER_REQUEST_USD = 0.40
HANDOFF_SUMMARY_MAX_TOKENS = 512  # Design §6b / Q2

# 模型分级（Proposal §2：路由/裁判/抽取用 Haiku，合成/出题/复盘用 Sonnet）
AGENT_MODEL_TIER: Dict[AgentId, ModelTier] = {
    AgentId.MANAGER: ModelTier.SONNET,
    AgentId.ROUTER: ModelTier.HAIKU,
    AgentId.SYNTHESIZER: ModelTier.SONNET,
    AgentId.VERIFIER: ModelTier.HAIKU,
    AgentId.RETRIEVAL: ModelTier.HAIKU,  # 仅条件改写
    AgentId.PLANNING: ModelTier.SONNET,
    AgentId.INTERVIEWER: ModelTier.SONNET,
    AgentId.JUDGE: ModelTier.HAIKU,
    AgentId.STRATEGIST: ModelTier.HAIKU,
    AgentId.COACH: ModelTier.SONNET,
    AgentId.DIAGNOSIS: ModelTier.SONNET,
}

# 各 agent Bounds（timeout_s, max_cost_usd）——Design §3 各节"Bounds"
AGENT_BOUNDS: Dict[AgentId, Dict[str, float]] = {
    AgentId.MANAGER: {"timeout_s": 60.0, "max_cost_usd": 0.40},
    AgentId.QA: {"timeout_s": 8.0, "max_cost_usd": 0.03},
    AgentId.ROUTER: {"timeout_s": 1.5, "max_cost_usd": 0.002},
    AgentId.SYNTHESIZER: {"timeout_s": 4.0, "max_cost_usd": 0.02},
    AgentId.VERIFIER: {"timeout_s": 3.0, "max_cost_usd": 0.008},
    AgentId.RETRIEVAL: {"timeout_s": 1.5, "max_cost_usd": 0.003},
    AgentId.PLANNING: {"timeout_s": 20.0, "max_cost_usd": 0.10},
    AgentId.MOCK: {"timeout_s": 900.0, "max_cost_usd": 0.30},
    AgentId.INTERVIEWER: {"timeout_s": 5.0, "max_cost_usd": 0.03},
    AgentId.JUDGE: {"timeout_s": 3.0, "max_cost_usd": 0.008},
    AgentId.STRATEGIST: {"timeout_s": 2.0, "max_cost_usd": 0.005},
    AgentId.COACH: {"timeout_s": 8.0, "max_cost_usd": 0.05},
    AgentId.DIAGNOSIS: {"timeout_s": 10.0, "max_cost_usd": 0.05},
}

# 检索融合参数（Design §7a/§7b，待压测调参 DG）
RRF_K = 60
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_SEARCH = 64
