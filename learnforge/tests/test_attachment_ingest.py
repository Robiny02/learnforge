"""附件入库 → 本地知识库（切片/去重/图片摘要）+ 统一检索接口（离线可回归）。

锁住：
- 文本附件解析后切片入库到 chunks(local)，可被 retrieval 召回（任务 1）。
- 重复上传同一文件不重复入库（按内容哈希去重，复用 document_id）。
- 图片入库存 metadata + image summary（vision 不可用时优雅降级，不崩）（任务 3）。
- DocumentRef/manifest 只存引用与摘要，不含全文；artifact 可经 document_id 回查（压缩约束）。
- BaseAgent.recall 统一检索接口可被各专家 agent 复用（任务 2）。
无 key 全程不崩（chain always passes）。
"""

from __future__ import annotations


from learnforge.contracts.agents.retrieval import RetrievalFilters
from learnforge.contracts.enums import KnowledgeScope
from learnforge.multimodal import ingest_attachments, parse_attachments
from learnforge.storage.repositories import ChunkRepository


def _md(name: str, text: str) -> dict:
    return {"filename": name, "mime": "text/markdown", "data": text}


# ----------------------------------------------------------------- 任务 1：文本入库
def test_text_attachment_chunks_into_local_kb(tmp_db: str):
    atts = parse_attachments([_md("note.md", "# RAG 笔记\n检索增强生成把向量召回与 LLM 合成结合。")])
    manifest = ingest_attachments(atts, session_id="s1", db_path=tmp_db)

    assert len(manifest.documents) == 1
    doc = manifest.documents[0]
    assert doc.kind == "markdown" and doc.chunk_count >= 1
    assert not doc.reused
    # 真实内容落 chunks(local)，可经全文检索召回。
    repo = ChunkRepository(db_path=tmp_db)
    hits = repo.fts_match("检索增强", top_k=5,
                          filters=RetrievalFilters(kb_scope=KnowledgeScope.LOCAL))
    assert any("检索增强" in h.text for h in hits)
    # 切片带 origin/document_id 元数据（供按来源/按文档回查）。
    assert repo.count_by_document(doc.document_id) == doc.chunk_count


def test_duplicate_upload_is_deduped(tmp_db: str):
    raws = [_md("dup.md", "重复内容：分布式锁要保证互斥与可重入。")]
    first = ingest_attachments(parse_attachments(raws), session_id="s", db_path=tmp_db)
    second = ingest_attachments(parse_attachments(raws), session_id="s", db_path=tmp_db)

    assert first.documents[0].reused is False
    assert second.documents[0].reused is True
    # 复用同一 document_id，没有重复切片入库。
    assert second.documents[0].document_id == first.documents[0].document_id
    repo = ChunkRepository(db_path=tmp_db)
    assert repo.count_by_document(first.documents[0].document_id) == first.documents[0].chunk_count


def test_manifest_holds_reference_not_fulltext(tmp_db: str):
    big = "段落。" * 2000  # 远超 manifest 摘要预算
    manifest = ingest_attachments(parse_attachments([_md("big.md", big)]),
                                  session_id="s", db_path=tmp_db)
    doc = manifest.documents[0]
    assert len(doc.summary) <= 600                 # manifest 只存轻量摘要
    assert len(doc.summary) < len(big)             # 不是全文
    art = doc.as_artifact()
    assert art["ref"] == doc.document_id and art["kind"] == "document"


# ----------------------------------------------------------------- 任务 3：图片入库
def test_image_ingest_offline_degrades_and_persists(tmp_db: str):
    atts = parse_attachments([
        {"filename": "arch.png", "mime": "image/png",
         "data": "data:image/png;base64,iVBORw0KGgo="},
    ])
    manifest = ingest_attachments(atts, session_id="s", db_path=tmp_db)
    doc = manifest.documents[0]
    assert doc.kind == "image"
    assert doc.degraded is True                    # 无 key → vision 摘要降级
    assert doc.chunk_count >= 1                     # 仍写入可检索摘要 chunk（含 metadata）
    repo = ChunkRepository(db_path=tmp_db)
    chunks = repo.fts_match("图片", top_k=5, filters=RetrievalFilters(document_id=doc.document_id))
    assert chunks  # 图片摘要可经 document_id 回查


def test_image_ingest_with_vision(tmp_db, monkeypatch):
    from learnforge.llm.client import LLM, LLMResult, TokenUsage
    from learnforge.contracts.enums import ModelTier

    def fake_complete(prompt, model_tier=ModelTier.SONNET, system=None, max_tokens=1024,
                      timeout_s=None, response_format=None, model=None, images=None):
        assert images and images[0].startswith("data:image/")
        return LLMResult(
            text=('{"title":"系统架构图","image_kind":"architecture",'
                  '"summary":"展示 Kafka 与消费者集群的消息流。","key_text":"Kafka, Consumer",'
                  '"topics":["消息队列"]}'),
            tokens=TokenUsage(prompt=0, completion=0), cost_usd=0.0, model_tier=model_tier)

    monkeypatch.setattr(LLM, "available", True, raising=False)
    monkeypatch.setattr(LLM, "complete", fake_complete, raising=False)

    atts = parse_attachments([
        {"filename": "arch.png", "mime": "image/png",
         "data": "data:image/png;base64,iVBORw0KGgo="},
    ])
    manifest = ingest_attachments(atts, session_id="s", db_path=tmp_db)
    doc = manifest.documents[0]
    assert doc.degraded is False and doc.image_kind == "architecture"
    # 「根据图片内容/我上传的架构图」→ summary 进 KB，可经关键词召回。
    repo = ChunkRepository(db_path=tmp_db)
    hits = repo.fts_match("Kafka", top_k=5,
                          filters=RetrievalFilters(kb_scope=KnowledgeScope.LOCAL))
    assert any("Kafka" in h.text for h in hits)


# ----------------------------------------------------------------- 任务 2：统一检索接口
def test_recall_interface_finds_uploaded_material(tmp_db: str):
    from learnforge.agents.planning.planning_agent import PlanningAgent

    ingest_attachments(parse_attachments([_md("jd.md", "岗位要求：精通 Redis 缓存与分布式锁。")]),
                       session_id="s", db_path=tmp_db)
    agent = PlanningAgent(db_path=tmp_db)
    res = agent.recall("Redis 缓存", scopes=[KnowledgeScope.LOCAL], top_k=4)
    assert res.text.startswith("【召回材料】")
    assert "Redis" in res.text


def test_recall_by_origin_filter(tmp_db: str):
    """origin=attachment 只召回上传材料，不混入其它本地记忆。"""
    from learnforge.agents.mock.interviewer import InterviewerAgent

    ingest_attachments(parse_attachments([_md("resume.md", "项目经历：用 Kafka 搭建实时风控。")]),
                       session_id="s", db_path=tmp_db)
    agent = InterviewerAgent(db_path=tmp_db)
    res = agent.recall("Kafka", scopes=[KnowledgeScope.LOCAL], origin="attachment", top_k=3)
    assert "Kafka" in res.text


def test_recall_empty_query_is_noop(tmp_db: str):
    from learnforge.agents.diagnosis.diagnosis_agent import DiagnosisAgent

    agent = DiagnosisAgent(db_path=tmp_db)
    res = agent.recall("", scopes=[KnowledgeScope.LOCAL])
    assert res.text == "" and res.chunks == []


# ----------------------------------------------------------------- server 端到端
def test_server_attachment_ingests_and_records_artifact(tmp_db, monkeypatch):
    from learnforge.llm import client
    monkeypatch.setattr(client.LLM, "available", False, raising=False)

    from learnforge.app import server
    from learnforge.orchestration.manager import ManagerAgent
    # 把全局 manager 指到 tmp_db，避免污染默认库，并让入库/会话账本都落 tmp_db。
    monkeypatch.setattr(server, "_manager", ManagerAgent(db_path=tmp_db), raising=False)

    req = server.UIChatRequest(
        text="这份架构说明讲了啥", mode="qa", session_id="t-att-ingest",
        attachments=[_md("design.md", "系统用 Kafka 做消息队列，消费者幂等去重。")],
    )
    body = server._ui_chat_dispatch(req)
    assert body["status"] == "ok"
    assert body["plan"][-1]["documents"] == 1
    assert body["documents"][0]["chunk_count"] >= 1
    # 入库后内容可检索。
    repo = ChunkRepository(db_path=tmp_db)
    hits = repo.fts_match("幂等", top_k=5,
                          filters=RetrievalFilters(kb_scope=KnowledgeScope.LOCAL))
    assert any("幂等" in h.text for h in hits)

    # artifact 落 dialogue_turns，可经 document_id 回查。
    server._ctx_record(req.session_id, req.text, body)
    from learnforge.storage.repositories import DialogueTurnRepository
    turns = DialogueTurnRepository(db_path=tmp_db).recent(req.session_id, limit=5)
    arts = [a for t in turns for a in t.artifacts if a.get("kind") == "document"]
    assert arts and arts[0]["ref"] == body["documents"][0]["document_id"]
