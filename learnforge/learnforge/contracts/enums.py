"""全局枚举（Phase 1 先定死，后续所有 agent 共用）。

对应 Design §3 / §4。所有枚举继承 str 以便直接 JSON 序列化与 SQLite 存储。
"""

from enum import Enum


class AgentId(str, Enum):
    """agent 的稳定标识（Design §2a 职责矩阵）。"""

    MANAGER = "manager"
    QA = "qa"
    ROUTER = "router"
    SYNTHESIZER = "synthesizer"
    VERIFIER = "verifier"
    RETRIEVAL = "retrieval"
    PLANNING = "planning"
    MOCK = "mock"
    INTERVIEWER = "interviewer"
    JUDGE = "judge"
    STRATEGIST = "strategist"
    COACH = "coach"
    DIAGNOSIS = "diagnosis"
    EVIDENCE = "evidence"  # 统一只读证据 worker（EvidenceResearchAgent），由 Manager 调用


class MsgType(str, Enum):
    """AgentMessage 信封类型（Design §4a）。"""

    TASK = "task"
    RESPONSE = "response"
    EVENT = "event"


class Status(str, Enum):
    """ResponsePayload 状态（Design §4a / §8）。"""

    OK = "ok"
    ERROR = "error"
    PARTIAL = "partial"
    NEEDS_INPUT = "needs_input"
    ESCALATE = "escalate"


class QType(str, Enum):
    """RouterAgent 问题类型（Design §3.3）。"""

    CONCEPT = "concept"
    PROJECT = "project"
    TEMPORAL = "temporal"
    CHITCHAT = "chitchat"


class Verdict(str, Enum):
    """VerifierAgent / QAAgent 事实把关结论（Design §3.5）。"""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    UNCERTAIN = "uncertain"


class StrategistAction(str, Enum):
    """StrategistAgent 决策动作（Design §3.11 / Q4 中断边界）。"""

    CONTINUE = "continue"
    RAISE = "raise"
    LOWER = "lower"
    SWITCH_TOPIC = "switch_topic"
    PAUSE = "pause"
    END = "end"
    ESCALATE = "escalate"


class InterviewPhase(str, Enum):
    """模拟面试环节（蒸馏自 tech-interview skill 的三环节）。Director 据答题推进。"""

    BASICS = "basics"                # 基础知识
    PROJECT = "project"              # 项目深挖
    SYSTEM_DESIGN = "system_design"  # 系统设计 / 编码


class InterviewMove(str, Enum):
    """InterviewDirector 的动作空间（替代旧 Strategist + 状态机固定边路由）。

    比 StrategistAction 更丰富：含证据式追问 / 简历诚信点破 / 高质量解答 / 纠错 / 空转检查。
    路由由 Director 智能选取（Haiku + 确定性兜底），生成交给 Interviewer（强模型）。
    """

    ASK = "ask"                  # 出新题（必要时推进环节）
    FOLLOWUP = "followup"        # 顺着上一轮回答深挖
    PROBE = "probe"              # 简历诚信：答不出简历写的内容 → 点破
    HINT = "hint"                # 导师模式给思路（不给答案）
    REVEAL = "reveal"            # 公布高质量参考答案
    CORRECT = "correct"          # 答错 → 高质量纠正后继续
    RAISE = "raise"              # 升难度
    LOWER = "lower"              # 降难度
    SWITCH_TOPIC = "switch_topic"
    CHECK_CONTINUE = "check_continue"  # 空转≥阈值 → 主动问继续拷打还是总结
    SUMMARIZE = "summarize"      # 收尾 → 终场复盘
    ESCALATE = "escalate"        # 跨能力 → 交回 Manager
    PAUSE = "pause"


class PlanMode(str, Enum):
    """PlanningAgent 模式（Design §3.7，生成+修改合并）。"""

    GENERATE = "generate"
    MODIFY = "modify"


class MasteryTier(str, Enum):
    """Knowledge Atom 掌握度分档（Design §4d，Q1 离散映射）。"""

    UNKNOWN = "unknown"
    LEARNING = "learning"
    FAMILIAR = "familiar"
    PROFICIENT = "proficient"
    MASTERED = "mastered"


class EventType(str, Enum):
    """interaction_events 事件类型（Design §4a / §4c）。"""

    QA_SIGNAL = "qa_signal"
    MOCK_WEAKNESS = "mock_weakness"
    PATH_CHANGED = "path_changed"


class SourceType(str, Enum):
    """检索语料来源类型（Design §7c）。"""

    INTERVIEW_POST = "interview_post"
    BLOG = "blog"
    DOC = "doc"
    ATOM = "atom"


class TimeWindow(str, Enum):
    """DiagnosisAgent 聚合时间窗（Design §3.13）。"""

    D7 = "7d"
    D30 = "30d"
    ALL = "all"


class DiagnosisTrigger(str, Enum):
    """诊断触发来源（Design §5.5，Q5 自动触发）。"""

    USER = "user"
    POST_MOCK = "post_mock"
    COMPOSITE = "composite"


class RetrievalBackend(str, Enum):
    """RetrievalAgent 可插拔后端（Design §3.6 / §10a G1）。

    历史字段，保留向后兼容；新代码改用 `KnowledgeScope` + `RetrievalMethod`。
    映射约定：LOCAL → 本地用户库；CLOUD → 远程共享库。
    """

    LOCAL = "local"
    CLOUD = "cloud"


class KnowledgeScope(str, Enum):
    """知识源所属层级（双层知识体系）。

    - LOCAL：本地用户库——个人学习数据（mock 记录、历史问答等）。
    - SHARED：共享知识库——可复用公共内容（课程/博客/面经/文档切片/公共知识点）。
    """

    LOCAL = "local"
    SHARED = "shared"


class RetrievalMethod(str, Enum):
    """统一检索方式（RetrievalAgent 入口选择）。

    - KEYWORD：关键词/子串匹配（LIKE 降级路径，CJK 友好）。
    - FULLTEXT：FTS5 BM25 全文检索。
    - VECTOR：sqlite-vec 向量 KNN（需 embedding 可用）。
    - HYBRID：全文 + 向量并召回后 RRF 融合（默认）。
    """

    KEYWORD = "keyword"
    FULLTEXT = "fulltext"
    VECTOR = "vector"
    HYBRID = "hybrid"


class RetrievalMode(str, Enum):
    """RetrievalAgent 内部运行模式。

    - WORKFLOW：固定检索流水线（当前默认行为）。
    - WORKFLOW_V2：固定流水线 + 失败后受控改写 + intent gate。
    - REACT：Thought/Action/Observation 小回路，按观察结果重试改写/降级。
    """

    WORKFLOW = "workflow"
    WORKFLOW_V2 = "workflow_v2"
    REACT = "react"


class ModelTier(str, Enum):
    """模型分级（Proposal §2 成本目标：Haiku 高频小推理 / Sonnet 合成）。"""

    HAIKU = "haiku"
    SONNET = "sonnet"


class ResumeIssueCategory(str, Enum):
    """简历问题分类（蒸馏自 llm-intern-skill 的 resume review）。

    - UNSUPPORTED_CLAIM：声称做过但无证据支撑的论断。
    - WEAK_PHRASING：技术表达含糊/不精确，撑不住追问。
    - RISKY_LANGUAGE：夸大/结果性措辞（主导/上线/显著提升），易被问穿。
    - EVIDENCE_GAP：缺少能力佐证（指标/数据/对比/产物）。
    - INTERVIEW_VULNERABILITY：最容易在面试中被攻破的薄弱点。
    """

    UNSUPPORTED_CLAIM = "unsupported_claim"
    WEAK_PHRASING = "weak_phrasing"
    RISKY_LANGUAGE = "risky_language"
    EVIDENCE_GAP = "evidence_gap"
    INTERVIEW_VULNERABILITY = "interview_vulnerability"


class IssueSeverity(str, Enum):
    """简历问题严重度。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class JDFitVerdict(str, Enum):
    """简历与目标岗位的匹配判定（llm-intern-skill: JD Fit Verdict）。"""

    RISKY = "risky"
    MEDIUM = "medium"
    STRONG = "strong"
    UNKNOWN = "unknown"


class ExternalSourceKind(str, Enum):
    """简历/输入里外部链接的类型（决定怎么挖证据）。"""

    GITHUB_REPO = "github_repo"      # owner/repo 根：读 summary/README/CLAUDE.md + 按 claim 找源码/测试
    GITHUB_FILE = "github_file"      # /blob/.../file：直接读该文件
    GITHUB_DIR = "github_dir"        # /tree/.../dir：列目录按 claim 选文件
    TECH_BLOG = "tech_blog"          # 技术博客：fetch 正文（只算"项目说明"，非源码级证据）
    DOCS_PAGE = "docs_page"          # 文档站（gitbook/notion/readthedocs）：fetch 作文档证据
    UNKNOWN_URL = "unknown_url"      # 其它：尝试 fetch，失败记录原因


class EvidenceSourceType(str, Enum):
    """EvidenceResearchAgent 支持的证据来源类型（统一只读 worker 读取不同 source）。

    第一阶段统一一个证据 worker；后续若 repo/简历/文档分析复杂化，再拆成
    RepoResearchAgent / ResumeResearchAgent / DocumentResearchAgent。
    """

    RESUME = "resume"            # 简历正文 / 已存简历诊断（resume.recall）
    REPO = "repo"               # 工作区源码关键词搜索（repo.search）
    FILE = "file"               # 工作区内具体文件（file.read）
    ATTACHMENT = "attachment"    # 上传附件材料（attachment.recall）


class ClaimType(str, Enum):
    """简历 claim 的类型（项目级诊断先分类再拷打）。

    - ARCHITECTURE：架构/系统设计 claim（如"分层 Agent 架构""唯一写者"）。
    - IMPLEMENTATION：具体实现 claim（用了什么算法/机制/数据结构）。
    - METRIC：指标/效果 claim（提升 X%/QPS/准确率）。
    - CONTRIBUTION：个人贡献/角色 claim（我负责/主导哪部分）。
    - TECH_STACK：技术栈背景（用到的语言/框架/中间件枚举）——**不单独当风险点**。
    """

    ARCHITECTURE = "architecture"
    IMPLEMENTATION = "implementation"
    METRIC = "metric"
    CONTRIBUTION = "contribution"
    TECH_STACK = "tech_stack"


class EvidenceStrength(str, Enum):
    """claim 的证据支持**来源**（按可验证强度区分，不用笼统的 strong/weak）。

    - NONE：无任何材料支撑。
    - DOC_SUPPORTED：仅 README/CLAUDE.md/设计文档等**文档**支持（最弱，文档说有≠真做到）。
    - CODE_SUPPORTED：有**源码**支持（能指到具体文件/函数）。
    - TEST_SUPPORTED：有**测试**支持（测试覆盖该行为）。
    - RUNTIME_SUPPORTED：有 **trace/benchmark/demo** 等运行期证据（最强）。

    只读到 README/CLAUDE.md 时最多判 DOC_SUPPORTED，不要轻易判为 code/test/runtime。
    """

    NONE = "none"
    DOC_SUPPORTED = "doc_supported"
    CODE_SUPPORTED = "code_supported"
    TEST_SUPPORTED = "test_supported"
    RUNTIME_SUPPORTED = "runtime_supported"
