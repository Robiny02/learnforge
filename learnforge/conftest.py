"""pytest 共享 fixtures（最小测试套件，Tasks §6）。

测试均离线运行（无 ANTHROPIC_API_KEY）：依赖各 agent 的确定性兜底，断言结构/契约/闭环/只读性，
不断言 LLM 输出质量（质量门槛留 golden set，见 Tasks T-EV-*）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

# 测试强制离线：先把 key 置空再 import learnforge（client.py 的 load_dotenv(override=False)
# 不会覆盖已存在的环境变量），使 LLM.available=False、各 agent 走确定性兜底，断言可复现、不联网/计费。
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""

# 确保 learnforge 包可导入（conftest 所在目录会被 pytest 加入 sys.path）。
sys.path.insert(0, str(Path(__file__).parent))

from learnforge.contracts.atom import KnowledgeAtom  # noqa: E402
from learnforge.contracts.enums import EventType  # noqa: E402
from learnforge.contracts.message import EventPayload  # noqa: E402
from learnforge.storage.db import init_db  # noqa: E402
from learnforge.storage.repositories import (  # noqa: E402
    AtomRepository,
    ChunkRepository,
    EventRepository,
    QAHistoryRepository,
)


@pytest.fixture
def tmp_db() -> str:
    """空库（仅建表）。"""
    path = os.path.join(tempfile.mkdtemp(), "lf.db")
    init_db(path)
    return path


@pytest.fixture
def seeded_db(tmp_db: str) -> str:
    """种子库：concurrency/database 两类 atom + 多条弱点事件（供诊断/复合用）。"""
    now = datetime.now(timezone.utc)
    ar = AtomRepository(db_path=tmp_db)
    for i, (topic, title) in enumerate(
        [("concurrency", "乐观锁vs悲观锁"), ("concurrency", "线程池"), ("database", "B+树索引")]
    ):
        ar.upsert(KnowledgeAtom(atom_id=f"a{i}", topic=topic, title=title,
                                canonical_question="?", mastery_score=0.2,
                                created_at=now, updated_at=now))
    er = EventRepository(db_path=tmp_db)
    for i in range(4):
        er.append(EventPayload(event_type=EventType.MOCK_WEAKNESS, topic="concurrency",
                               atom_refs=["a0", "a1"], signal={"weak": True}), event_id=f"e{i}")
    er.append(EventPayload(event_type=EventType.QA_SIGNAL, topic="database",
                           atom_refs=["a2"], signal={"correct": False}), event_id="eq")

    # 共享知识库语料（chunks）+ 本地用户库历史问答（qa_history），供检索用例。
    cr = ChunkRepository(db_path=tmp_db)
    cr.upsert(chunk_id="c0", text="乐观锁通过版本号实现并发控制，适用于读多写少场景。",
              source_type="doc", topic="concurrency", kb_scope="shared")
    cr.upsert(chunk_id="c1", text="B+树索引在数据库中用于范围查询与排序优化。",
              source_type="blog", topic="database", kb_scope="shared")
    qr = QAHistoryRepository(db_path=tmp_db)
    qr.append(qa_id="q0", question="什么是乐观锁？", answer="基于版本号的并发控制策略。",
              topic="concurrency")
    return tmp_db
