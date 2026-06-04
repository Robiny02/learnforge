"""多模态附件：解析层 + vision 组装 + QA 接入 + server 入口（离线可回归）。

锁住：MD/TXT/图片解析、PDF 优雅降级、vision content 数组、附件文本进证据槽+图片走 vision、
server 带附件路由到 QA。无 key 全程不崩（chain always passes）。
"""

from __future__ import annotations

import base64
import io

from learnforge.contracts.agents.qa import QAInput
from learnforge.contracts.attachment import flatten_attachments
from learnforge.contracts.enums import ModelTier
from learnforge.multimodal import parse_attachments


# ----------------------------------------------------------------- 解析层
def test_parse_md_and_text():
    txt_b64 = base64.b64encode("纯文本内容".encode()).decode()
    atts = parse_attachments([
        {"filename": "n.md", "mime": "text/markdown", "data": "# 标题\n正文"},  # 明文直传
        {"filename": "a.txt", "mime": "text/plain",
         "data": f"data:text/plain;base64,{txt_b64}"},  # 前端 readAsDataURL 的形态
    ])
    assert atts[0].kind == "markdown" and "正文" in atts[0].extracted_text
    assert atts[1].kind == "text" and atts[1].extracted_text == "纯文本内容"


def test_parse_image_keeps_data_url():
    atts = parse_attachments([
        {"filename": "x.png", "mime": "image/png", "data": "data:image/png;base64,iVBORw0KGgo="},
    ])
    assert atts[0].kind == "image" and atts[0].is_image()
    assert atts[0].image_data_url.startswith("data:image/png;base64,")
    assert atts[0].extracted_text == ""  # 图片不抽文本，交 vision


def test_parse_pdf_blank_degrades_gracefully():
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    atts = parse_attachments([{"filename": "d.pdf", "mime": "application/pdf",
                               "data": base64.b64encode(buf.getvalue()).decode()}])
    assert atts[0].kind == "pdf"
    assert atts[0].degraded and atts[0].extracted_text == ""  # 空白页→空文本→降级，但不崩


def test_flatten_splits_text_and_images():
    atts = parse_attachments([
        {"filename": "n.md", "mime": "text/markdown", "data": "RAG 笔记"},
        {"filename": "x.png", "mime": "image/png", "data": "data:image/png;base64,iVBORw0KGgo="},
    ])
    flat = flatten_attachments(atts)
    assert "RAG 笔记" in flat.text and "【附件：n.md】" in flat.text
    assert len(flat.images) == 1


# ----------------------------------------------------------------- vision 组装
def test_complete_builds_vision_content_array(monkeypatch):
    from learnforge.llm.client import LLM
    cap = {}

    class FakeHttpx:
        def post(self, url, headers=None, json=None, timeout=None):
            cap["payload"] = json

            class R:
                def raise_for_status(self): pass
                def json(self):
                    return {"choices": [{"message": {"content": "ok"}}], "usage": {}}
            return R()

    monkeypatch.setattr(LLM, "available", True, raising=False)
    monkeypatch.setattr(LLM, "_httpx", FakeHttpx(), raising=False)
    monkeypatch.setattr(LLM, "_api_key", "x", raising=False)
    LLM.complete("看图", model_tier=ModelTier.HAIKU, images=["data:image/png;base64,AAA"])
    content = cap["payload"]["messages"][-1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"


# ----------------------------------------------------------------- QA 接入
def _accumulate_llm(monkeypatch):
    from learnforge.llm.client import LLM, LLMResult, TokenUsage
    calls = []

    def fake_complete(prompt, model_tier=ModelTier.SONNET, system=None, max_tokens=1024,
                      timeout_s=None, response_format=None, model=None, images=None):
        calls.append({"prompt": prompt, "images": images})
        return LLMResult(text='{"draft":"答案","claims":[]}',
                         tokens=TokenUsage(prompt=0, completion=0), cost_usd=0.0,
                         model_tier=model_tier)

    monkeypatch.setattr(LLM, "available", True, raising=False)
    monkeypatch.setattr(LLM, "complete", fake_complete, raising=False)
    return calls


def test_qa_threads_attachment_text_and_images(monkeypatch):
    from learnforge.agents.qa import QAAgent
    calls = _accumulate_llm(monkeypatch)
    QAAgent().run(QAInput(question="看这张架构图哪里有瓶颈",
                          attachment_text="【附件：设计.md】\n系统用 Kafka 做消息队列",
                          image_data_urls=["data:image/png;base64,IMG"]))
    assert any("Kafka 做消息队列" in c["prompt"] for c in calls)       # 文本进证据
    assert any(c["images"] == ["data:image/png;base64,IMG"] for c in calls)  # 图片走 vision


def test_qa_with_attachments_offline_does_not_crash(monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    from learnforge.agents.qa import QAAgent
    out = QAAgent().run(QAInput(question="读附件", attachment_text="【附件：a.md】\n内容"))
    assert out.answer  # 无 key 也返回(stub)，不崩


# ----------------------------------------------------------------- server 入口
def test_server_attachment_routes_to_qa(monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)
    from learnforge.app.server import UIChatRequest, _ui_chat_dispatch
    body = _ui_chat_dispatch(UIChatRequest(
        text="这份笔记讲了啥", mode="qa", session_id="t-att",
        attachments=[{"filename": "n.md", "mime": "text/markdown", "data": "# RAG\n检索增强"}],
    ))
    assert body["status"] == "ok"
    assert body["plan"][-1]["agent"] == "qa" and body["plan"][-1]["attachments"] == 1
