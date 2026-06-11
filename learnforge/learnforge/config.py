"""全局配置：模型分级 + 各 agent Bounds（Design §3 / Proposal §2 成本目标）。

Phase 1 仅作为常量与查表，不接入真实 client。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Dict

from .contracts.enums import AgentId, ModelTier


def _load_local_env() -> None:
    """Load local .env values without requiring python-dotenv."""

    seen = set()
    for base in [Path.cwd(), *Path(__file__).resolve().parents]:
        env_path = base / ".env"
        if env_path in seen or not env_path.exists():
            continue
        seen.add(env_path)
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_local_env()

# 数据库
DB_PATH = "learnforge.db"

# ---------------------------------------------------------------------------
# 记忆系统（OpenClaw 风格：MEMORY.md + daily markdown + 索引召回 + 衰减）
# ---------------------------------------------------------------------------
# 记忆文件根目录（运行时目录）：MEMORY.md（稳定规则）+ YYYY-MM-DD.md（每日记录）。
MEMORY_DIR = os.getenv("LF_MEMORY_DIR", "data/memory")
# daily 召回时间衰减率 λ（exp(-λ·days)，镜像 mastery.effective_mastery）。
#   普通每日记录半衰期≈7天；薄弱点/mock 反馈衰减慢，半衰期≈30天。MEMORY.md 不衰减。
MEMORY_DECAY_FAST = math.log(2) / 7.0
MEMORY_DECAY_SLOW = math.log(2) / 30.0
# daily memory 支持的记忆类型（kind）。Agent 判定沉淀；daemon 为后续异步扩展位。
#   qa=问答总结 weak=薄弱点 mock=mock 反馈 progress=学习进展 decision=阶段性项目决策 note=普通记录
MEMORY_KINDS = ("qa", "weak", "mock", "progress", "decision", "note")
# 衰减慢（“黏性”/高重要性）的 note 类型；其余按 FAST。
#   薄弱点/mock 反馈/学习进展/项目决策都比普通问答更重要，应衰减更慢、召回权重更高（REQUIREMENTS R4.7）。
MEMORY_STICKY_KINDS = ("weak", "mock", "progress", "decision")
# 各 kind 的基础重要性 ∈[0,1]，作为召回排序的次要因子（REQUIREMENTS R4.2/R7.3）。
#   缺省 kind → MEMORY_IMPORTANCE_DEFAULT。
MEMORY_IMPORTANCE = {
    "decision": 1.0,
    "weak": 0.9,
    "progress": 0.8,
    "mock": 0.8,
    "qa": 0.5,
    "note": 0.4,
}
MEMORY_IMPORTANCE_DEFAULT = 0.5
# session_memory 压缩 = Claude Code 式会话级 compaction（按 token 阈值，不按轮数）：每个 agent
# turn 结束统计 session context tokens（summary + 最近原文轮，不含 pinned），超 THRESHOLD 才把较
# 旧轮折叠进 summary，压到 ≤TARGET，但至少保留 MIN_RECENT 轮。
#
# 阈值按**项目上下文 token 上限的占用率**派生（Claude Code 语义，不是拍脑袋的小绝对值）：
#   - CONTEXT_LIMIT_TOKENS：项目上下文 token 上限。默认 = 所用模型窗口（两档默认 openai/
#     gpt-4o-mini = 128K）；用 LF_CONTEXT_LIMIT_TOKENS 覆盖（换 claude-3.5-sonnet 设 200000，
#     或想更省成本/更早压缩就调小）。
#   - 占上限 80% 触发 compaction，压到 35%（落在 30–40% 区间中值）。
# 默认 128K：THRESHOLD=102400、TARGET=44800，约 100 轮才触发、压到约 45 轮；日常对话几乎不触发，
# compaction 只在接近上限时兜底，pinned + MIN_RECENT 兜住重要/最近内容。
CONTEXT_LIMIT_TOKENS = int(os.getenv("LF_CONTEXT_LIMIT_TOKENS", "128000"))
SESSION_COMPACTION_TRIGGER_RATIO = 0.80   # 占上限 80% → 触发
SESSION_COMPACTION_TARGET_RATIO = 0.35    # 压到约 35%（30–40% 区间）
SESSION_COMPACTION_THRESHOLD_TOKENS = round(CONTEXT_LIMIT_TOKENS * SESSION_COMPACTION_TRIGGER_RATIO)
SESSION_COMPACTION_TARGET_TOKENS = round(CONTEXT_LIMIT_TOKENS * SESSION_COMPACTION_TARGET_RATIO)
SESSION_MIN_RECENT_ROUNDS = 2
# 重要结果保护：标 important 的轮进 pinned 区，**永不被 compaction 折叠**，渲染时全文置顶注入。
# FIFO 上限 + 单条长度上限防膨胀；pinned 不计入触发阈值（不可压，计入会导致压不下去）。
SESSION_MAX_PINNED = 8
SESSION_PIN_MAX_CHARS = 500
# 每折叠 N 次，做一次「全量结构化重摘」(LLM-only)：把滚动 summary 重新组织成
# 目标/决策/未决项/主题，修正增量折叠的漂移与碎片化。离线(无 key)不触发，保持纯增量(§14-7)。
SESSION_RESUMMARIZE_EVERY = 3
# 记忆召回相似度闸门（仅对 cosine 相似度分数有意义，即 vector/hybrid 模式；
# FTS 的 RRF 位置分不是相似度，不适用本闸门）。
#   - MIN_SIM：top1 低于它 → 判“没有找到明确记忆”（不编造）。
#   - MARGIN：top1-top2 过近 → 不直接采纳，交 rerank / 保守处理。
# ⚠️ 默认值来自小规模 adversarial eval（n≈16），仅为初始经验值，需更大评测集校准，勿当通用常数。
MEMORY_RECALL_MIN_SIM = float(os.getenv("LF_MEMORY_RECALL_MIN_SIM", "0.33"))
MEMORY_RECALL_MARGIN = float(os.getenv("LF_MEMORY_RECALL_MARGIN", "0.05"))
MEMORY_RECALL_THRESHOLD_SOURCE = "small-scale adversarial eval (n≈16); provisional, recalibrate"
# PostgreSQL 知识库连接串（可选）。未设置 → 仅用 SQLite 路径（fallback 保留）。
POSTGRES_DSN = os.getenv("LF_POSTGRES_DSN")

# Embedding / 向量检索（外部 API，可插拔；无 key 时离线降级到 FTS）
# 维度经 env 覆盖；vec0 schema 按此维度建表，请保持与 provider 输出一致（默认 1024）。
EMBEDDING_DIM = int(os.getenv("LF_EMBEDDING_DIM", "1024"))
# provider ∈ {none, openai, voyage}；none（默认）= 离线，不做向量检索。
EMBEDDING_PROVIDER = os.getenv("LF_EMBEDDING_PROVIDER", "none").lower()
# OpenAI 兼容端点 base_url（可选）。设了即走该网关，支持 OpenAI 兼容代理（如 OpenRouter：
#   LF_OPENAI_BASE_URL=https://openrouter.ai/api/v1 + LF_EMBEDDING_MODEL=openai/text-embedding-3-small）。
OPENAI_BASE_URL = os.getenv("LF_OPENAI_BASE_URL") or None
# 不同 provider 的默认模型；openai 的 text-embedding-3-* 支持 dimensions 截断到 EMBEDDING_DIM。
_DEFAULT_EMBEDDING_MODEL = {
    "openai": "text-embedding-3-small",
    "voyage": "voyage-3",
}
EMBEDDING_MODEL = os.getenv(
    "LF_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL.get(EMBEDDING_PROVIDER, "")
)

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
