"""BlockClassifierAgent — Phase 3 (Plan §3).

Assigns each block one of 8 semantic labels and an index_role:
- main         : technical_core, useful_ocr, useful_context
- downweighted : career_noise, off_topic_job   (kept, retrievable at low weight)
- isolated     : irrelevant_ocr, ad_or_promo, empty_or_garbled (kept, off main index)

Rule-based & deterministic (offline). When an LLM is available it may re-check the
*ambiguous* blocks only (cheap, conservative: never demotes a tech-keyword block).
Nothing is deleted — noise is routed, so noise-robustness stays measurable.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from ..chunkers.semantic import TOPIC_KEYWORDS
from ..lib.schema import BLOCK_TYPES, Block, Document
from .agent_base import LLMGateway, PipelineAgent

# Flatten tech vocabulary for "is this technical?" checks.
_TECH = sorted({kw for kws in TOPIC_KEYWORDS.values() for kw in kws})
_CODECUE = re.compile(r"[{}();]|def |class |->|复杂度|时间复杂度|O\(")
_PERSONAL = ["奶茶", "早睡", "脑子糊", "求好运", "好运", "玄学", "心态", "焦虑", "秒挂",
             "oc", "被捞", "timeline", "时间线", "速通", "祈祷", "许愿"]
_OFFTOPIC = ["产品经理", "运营", "销售", "管培生", "非技术岗", "需求分析", "竞品", "hr"]
_AD = ["公众号", "推荐阅读", "版权", "引流", "关注", "领取", "未经允许", "转载"]
_LOWVALUE_COMMENT = ["求模板", "简历模板", "求部门", "问部门", "私信", "求捞", "蹲"]

ROLE_OF = {
    "technical_core": "main",
    "useful_ocr": "main",
    "useful_context": "main",
    "career_noise": "downweighted",
    "off_topic_job": "downweighted",
    "irrelevant_ocr": "isolated",
    "ad_or_promo": "isolated",
    "empty_or_garbled": "isolated",
}


_NEGATION = ["没有问", "没问", "没问到", "不是技术", "没考", "未问", "都没问"]


def _has(text: str, words) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def _is_tech(text: str) -> bool:
    return _has(text, _TECH) or bool(_CODECUE.search(text))


def _tech_strength(text: str):
    """Return (is_tech, strong). `strong` ignores generic words & negated mentions
    — used to stop off-topic docs leaking on "项目" or "完全没有问 MySQL"."""
    low = text.lower()
    if _has(text, _NEGATION):
        return False, False  # negated tech mention is not technical content
    buckets = sum(
        1 for kws in TOPIC_KEYWORDS.values() if any(k in low for k in kws)
    )
    code = bool(_CODECUE.search(text)) or _has(text, ["手撕", "leetcode"])
    is_tech = buckets > 0 or code
    strong = code or buckets >= 2  # generic single-bucket (e.g. "项目") isn't strong
    return is_tech, strong


class BlockClassifierAgent(PipelineAgent):
    name = "block_classifier"
    capabilities = ["classify.block", "llm.complete"]

    def __init__(self, llm: Optional[LLMGateway] = None, use_llm_recheck: bool = True) -> None:
        super().__init__(llm)
        self.use_llm_recheck = use_llm_recheck

    # --- whole-doc prior from metadata (off-topic job / timeline posts) --------
    def _doc_prior(self, doc: Document) -> Optional[str]:
        rel = str(doc.metadata.get("relevance", "")).lower()
        if rel == "off_topic_job":
            return "off_topic_job"
        if rel in ("career_timeline",) or doc.metadata.get("off_topic_type") == "timeline_only":
            return "career_noise"
        return None

    def _rule_label(self, block: Block, doc_prior: Optional[str]) -> str:
        st, text = block.structure_type, block.text
        if not text.strip() or re.fullmatch(r"[\W_]+", text.strip()):
            return "empty_or_garbled"
        if st == "code":
            return "technical_core"
        if st == "table":
            return "useful_context"
        if st == "ad" or _has(text, _AD):
            return "ad_or_promo"
        if st == "tags":
            return "useful_context"
        if st == "image_ocr":
            if _is_tech(text):
                return "useful_ocr"            # conservative: keep tech OCR
            if _has(text, _PERSONAL):
                return "irrelevant_ocr"
            return "irrelevant_ocr" if not _is_tech(text) else "useful_ocr"
        if st == "comment":
            if _is_tech(text):
                return "technical_core"
            if _has(text, _LOWVALUE_COMMENT):
                return "career_noise"
            return "useful_context"
        # heading / paragraph / other
        is_tech, strong = _tech_strength(text)
        if doc_prior == "off_topic_job":
            # In a non-CS post, only strong tech evidence escapes downweighting.
            return "technical_core" if strong else "off_topic_job"
        if is_tech:
            return "technical_core"
        if _has(text, _OFFTOPIC):
            return "off_topic_job"
        if _has(text, _PERSONAL) or doc_prior == "career_noise":
            return "career_noise"
        return "useful_context"

    def _ambiguous(self, block: Block, label: str) -> bool:
        # Only re-check non-technical OCR / borderline paragraphs; never tech blocks.
        if block.structure_type == "image_ocr" and label == "irrelevant_ocr":
            return True
        if label in ("off_topic_job", "career_noise") and block.char_len > 60:
            return True
        return False

    def _llm_recheck(self, block: Block) -> Optional[str]:
        self.require("llm.complete")
        system = (
            "你是面经文本分块的分类器。只输出一个标签，取值范围："
            + ", ".join(BLOCK_TYPES)
            + "。technical_core/useful_ocr=含面试技术题或项目追问；"
            "off_topic_job=非技术岗内容；career_noise=个人感受/timeline；"
            "irrelevant_ocr=截图里的个人状态；ad_or_promo=广告引流。"
            "保守原则：只要含技术面试信息就归 technical_core/useful_ocr。"
        )
        out = self.llm.chat(system, block.text[:800], max_tokens=12)
        if not out:
            return None
        out = out.strip().lower()
        for t in BLOCK_TYPES:
            if t in out:
                return t
        return None

    def run(self, doc: Document) -> List[Block]:
        """Classify all blocks in-place (sets block.block_type) and return them."""
        self.require("classify.block")
        doc_prior = self._doc_prior(doc)
        for b in doc.blocks:
            label = self._rule_label(b, doc_prior)
            if (
                self.use_llm_recheck
                and self.llm.available
                and self._ambiguous(b, label)
            ):
                relabel = self._llm_recheck(b)
                # Guard: never let the LLM demote a clearly technical block.
                if relabel and not (_is_tech(b.text) and ROLE_OF[relabel] != "main"):
                    label = relabel
            b.block_type = label
            b.metadata["index_role"] = ROLE_OF[label]
        return doc.blocks

    @staticmethod
    def role_for(block: Block) -> str:
        return block.metadata.get("index_role") or ROLE_OF.get(block.block_type or "", "main")
