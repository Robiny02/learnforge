"""DiagnosisAgent 单测（Design §3.13 / §5.5）——只读性 + 弱点聚合 + 低置信声明。"""

from learnforge.agents.diagnosis import DiagnosisAgent
from learnforge.contracts.agents.diagnosis import DiagnosisInput
from learnforge.storage.db import get_connection

_TABLES = ["knowledge_atoms", "interaction_events", "learning_paths",
           "path_items", "diagnosis_reports"]


def _snapshot(db: str) -> dict:
    conn = get_connection(db)
    snap = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()] for t in _TABLES}
    conn.close()
    return snap


def test_empty_db_low_confidence(tmp_db):
    out = DiagnosisAgent(db_path=tmp_db).run(DiagnosisInput())
    assert out.confidence == 0.0
    assert out.clusters == [] and out.weak_atoms == []
    assert any("数据不足" in r for r in out.recommendations)


def test_weak_cluster_detection(seeded_db):
    agent = DiagnosisAgent(db_path=seeded_db)
    out = agent.run(DiagnosisInput())
    topics = {c.topic for c in out.clusters}
    assert "concurrency" in topics
    # concurrency 事件多于 database → severity 更高、排前
    assert out.clusters[0].topic == "concurrency"
    assert out.weak_atoms, "应识别出弱点 atom"
    assert out.confidence > 0.5
    # ReAct 三段式轨迹
    actions = [s["action"] for s in agent.last_react_trace]
    assert len(actions) == 3


def test_diagnosis_is_read_only(seeded_db):
    before = _snapshot(seeded_db)
    DiagnosisAgent(db_path=seeded_db).run(DiagnosisInput())
    after = _snapshot(seeded_db)
    assert before == after, "DiagnosisAgent 必须严格只读，不得写任何 state"
