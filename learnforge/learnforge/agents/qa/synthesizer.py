"""SynthesizerAgent —— 检索增强合成（Design §3.4）。Phase 2：Sonnet RAG + 无证据声明。"""

from __future__ import annotations

from ...contracts.agents.qa import SynthesizerInput, SynthesizerOutput
from ...contracts.enums import AgentId
from ..base import BaseAgent


# 「答题辅导」意图：用户问的不是"X 是什么"，而是"这道面试题/问题我该怎么回答"。
# 命中后切到示范答案模板（给可直接套用的话术 + 占位），而不是概念名词解释。
_INTERVIEW_COACH_CUES = (
    "怎么回答", "如何回答", "怎么答", "如何作答", "怎么作答", "怎么说", "如何说",
    "面试官问", "面试官让", "面试问", "被问到", "被问起", "问我", "如果问",
    "怎么聊", "如何展开", "怎么展开", "怎么讲", "如何讲这个", "这题怎么", "这道题怎么",
    "怎么准备这个问题", "如何回应",
)


def _is_interview_coaching(question: str) -> bool:
    q = (question or "").lower()
    return any(cue in q for cue in _INTERVIEW_COACH_CUES)


class SynthesizerAgent(BaseAgent):
    agent_id = AgentId.SYNTHESIZER

    def run(self, payload: SynthesizerInput) -> SynthesizerOutput:
        # 证据/上下文经 assembler 的有序槽位注入（retrieved → session → user_input），
        # 不再手工拼进 prompt——稳定 prefix 之后、缓存边界外，且消除各 agent 各拼一套的漂移。
        evidence = self._format_evidence(payload)
        atoms = ", ".join(a.title for a in payload.scoped_atoms) if payload.scoped_atoms else "无"
        retrieved_block = f"检索证据：\n{evidence}\n相关 Atom：{atoms}"
        # 用户附件文本(MD/PDF/TXT)作为自带证据并入 retrieved 槽；图片走 vision(见下方 images=)。
        if payload.attachment_text:
            retrieved_block += f"\n\n用户上传的附件内容：\n{payload.attachment_text}"
        session_block = (f"项目上下文：{payload.project_context}"
                         if payload.project_context else "")
        head = f"问题：{payload.question}\n"
        if payload.attachment_text and not _is_interview_coaching(payload.question):
            # 用户上传了文档（如开源 SKILL.md / 设计稿 / 简历 / JD）：要的是「把这份文档讲清楚」，
            # 不是套八股模板。基于完整文档内容做结构化深度讲解，覆盖文档实际写了什么。
            prompt = head + (
                "用户上传了一份文档，并希望你基于**完整文档内容**讲清楚它。绝不要套用八股概念模板，"
                "也不要只用一两句话泛泛概括——要忠实、具体、可执行地把这份文档拆开讲。\n"
                "请用 Markdown 分节，按文档实际内容组织（标题按需取舍，不必全有）：\n"
                "### 这是什么 —— 一句话定位 + 解决什么问题 / 适用场景。\n"
                "### 核心能力 —— 它能做的关键功能逐条列出（贴合文档里写的，不要编造）。\n"
                "### 使用流程 —— 按文档描述的步骤/工作流讲清怎么用（有命令/参数就带上真实示例）。\n"
                "### 关键参数与约定 —— 重要参数、命名规则、输入输出格式、限制条件。\n"
                "### 典型示例 —— 用文档里的或贴合文档的具体例子演示一次端到端用法。\n"
                "### 注意事项与边界 —— 坑、前置条件、不支持的场景、性能/缓存等注意点。\n"
                "### 一句话总结 —— 给一个可复述的电梯陈述。\n"
                "要求：内容必须来自上传文档；文档没提到的别硬编，可标注『文档未说明』。"
                "宁可详尽也不要空泛，覆盖文档的主要小节。输出 draft 与 claims。"
            )
            out = self.llm_structured(prompt, SynthesizerOutput, max_tokens=2304,
                                      retrieved=retrieved_block, session=session_block,
                                      images=payload.image_data_urls or None)
            if out is not None:
                return out
            return SynthesizerOutput(
                draft=(
                    "### 这是什么\n我读到了你上传的文档，但这次没能生成完整讲解。先把文档要点摘给你：\n\n"
                    f"{payload.attachment_text[:1600]}"
                ),
                claims=[],
            )
        if _is_interview_coaching(payload.question):
            prompt = head + (
                "用户在问『这道面试题/这类问题我该怎么回答』，要的是可以直接照着说的答案，"
                "不是名词解释。务必给出有具体内容的示范回答，绝不能只讲"
                "『描述你的角色、给出产物、分阶段说明』这类空泛的方法论。\n"
                "请用 Markdown 分节，按这些标题输出：\n"
                "### 考官在考什么 —— 这道题真正想考察的能力/信号（1-3 条）。\n"
                "### 答题框架 —— 一个可复用的结构（如 场景→具体怎么做→如何把关验证→收益与反思；"
                "经历题用 STAR）。\n"
                "### 可直接套用的示范回答 —— 写一段完整、口语化、有真实细节的样例答案，"
                "把需要本人填写的地方用【替换成你的真实项目/数据/工具】这样的占位标出；"
                "示范内容要具体（举出真实工具名、典型用法、可量化的收益），不能是空壳。\n"
                "### 加分点 —— 怎么让回答更可信：具体例子、量化指标、权衡取舍、踩过的坑。\n"
                "### 别这么答（扣分点）—— 常见雷区：泛泛而谈、夸大无证据、只说优点不谈把关/风险。\n"
                "### 可能的追问 —— 面试官大概率会追问什么，提前准备（2-4 条）。\n"
                "有检索证据就用来充实细节；无证据时凭通用工程常识给具体示范，但不要编造引用或杜撰数字"
                "（量化处用占位让用户填真实数据）。每节用短段落或 bullet。输出 draft 与 claims。"
            )
        else:
            prompt = head + (
                "基于证据合成回答；无证据则显式声明并弱化断言。\n"
                "回答应符合 LearnForge 的学习目标：不是短定义，而是可复习、可面试、可行动。\n"
                "高频八股/概念题至少覆盖 7 层：一句话结论、机制流程、关键配置/参数、"
                "故障窗口或边界条件、场景取舍、常见误区、面试追问/口述版。"
                "不要只写名词解释；要给工程上怎么选、为什么这么选、哪里会踩坑。\n"
                "请用 Markdown 分节，优先使用这些标题：### 核心结论、### 机制/流程、"
                "### 关键细节、### 场景与取舍、### 常见误区、### 面试追问、### 面试口述版。"
                "每节用短段落或 bullet，避免整段堆在一起。\n"
                "如果用户明确要求极简，再压缩到 1-3 句。输出 draft 与 claims。"
            )
        out = self.llm_structured(prompt, SynthesizerOutput, max_tokens=2304,
                                  retrieved=retrieved_block, session=session_block,
                                  images=payload.image_data_urls or None)
        if out is not None:
            return out
        # 回退：无 LLM 时给占位草稿（链路通）。
        if _is_interview_coaching(payload.question):
            return SynthesizerOutput(
                draft=(
                    "### 考官在考什么\n"
                    "- 你是否真的动手做过，而不是只会背概念；\n"
                    "- 你有没有判断力与把关意识（怎么验证、怎么兜底）。\n\n"
                    "### 答题框架\n"
                    "场景 → 具体怎么做 → 如何把关/验证 → 收益与反思。\n\n"
                    "### 可直接套用的示范回答\n"
                    "“在【替换成你的真实项目】里，我主要把它用在【替换成具体环节，如写单测/排查日志/重构】，"
                    "做法是【替换成具体步骤】；为避免它出错，我会【替换成你的验证方式，如跑测试、对照文档、code review】，"
                    "最后【替换成可量化收益，如把某任务耗时从 X 降到 Y】。”\n\n"
                    "### 加分点\n举一个具体例子 + 一个可量化收益 + 一处你主动发现并纠正它错误的经历。\n\n"
                    "### 别这么答\n泛泛说“提高效率”却没有例子；夸大成“全靠它”；只说优点不谈如何把关。\n\n"
                    "### 可能的追问\n它给的结果错了你怎么发现？哪些场景你不会用它？收益怎么衡量？"
                ),
                claims=[],
            )
        if payload.retrieved:
            snippets = "\n".join(
                f"- {c.text[:180]}" for c in payload.retrieved[:4]
            )
            return SynthesizerOutput(
                draft=(
                    f"基于本地知识库，我先给你一个可复习版回答：\n\n"
                    f"**核心结论**：{payload.question} 可以从定义、机制、适用场景和误区四层理解。\n\n"
                    f"**检索到的关键依据**：\n{snippets}\n\n"
                    "**面试表达建议**：先用一句话说清概念，再补机制和取舍；如果涉及生产系统，"
                    "最好补一个具体例子或失败场景。"
                ),
                claims=[],
            )
        note = "" if payload.retrieved else "（未检索到本地证据，以下为通用回答）"
        return SynthesizerOutput(draft=f"[stub synthesizer]{note}", claims=[])

    @staticmethod
    def _format_evidence(payload: SynthesizerInput) -> str:
        if not payload.retrieved:
            return "（无）"
        # 证据预算放宽：每片 200→400 字、最多 10 片——避免上下文过度保守导致回答太浅。
        return "\n".join(
            f"- [{c.chunk_id}] {c.text[:400]}" for c in payload.retrieved[:10]
        )
