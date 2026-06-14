"""Mock 状态持久化（P0，重构后）：跨进程重启可 resume + 缺失会话优雅过期不崩。

锁住（InterviewDirector + MockStateStore 替代旧 LangGraph checkpointer）：
- MockState 落 SQLite（同 db_path）：新建 agent 实例（≈进程重启）能继续上一场面试。
- 新 session_id = 开新面试，与旧会话互不干扰。
- 状态缺失（不同库 / 会话不存在）→ answer 返回 status=expired，不抛异常。
全离线、确定性。
"""

from __future__ import annotations

import os
import tempfile

from learnforge.agents.mock.mock_agent import MockInterviewAgent
from learnforge.contracts.agents.mock import MockInput
from learnforge.storage.db import init_db


def _fresh_db() -> str:
    p = os.path.join(tempfile.mkdtemp(), "lf.db")
    init_db(p)
    return p


# ----------------------------------------------------------------- 跨重启恢复
def test_interview_resumes_after_restart(tmp_db: str):
    sid = "sess-persist"

    # 进程 A：开场 + 答一轮，停在下一题（active）。状态落 SQLite（tmp_db）。
    a = MockInterviewAgent(db_path=tmp_db)
    assert a.persistent is True                                   # 确认状态落盘
    a.run(MockInput(topic="并发", session_id=sid))
    o = a.run(MockInput(topic="并发", session_id=sid, user_answer="我用乐观锁加版本号"))
    assert o.status == "active" and o.turn_index == 1

    # 进程 B：全新实例（≈重启）。同一 db_path → 从 MockStateStore 读回状态并继续作答。
    b = MockInterviewAgent(db_path=tmp_db)
    assert b.store.load(sid) is not None                          # 能读回中断态
    o2 = b.run(MockInput(topic="并发", session_id=sid, user_answer="补充：CAS 自旋重试"))
    assert o2.status == "active"
    assert o2.turn_index == 2                                     # 在上一场基础上继续推进
    assert o2.question                                           # 给出了下一题


def test_new_session_is_independent(tmp_db: str):
    a = MockInterviewAgent(db_path=tmp_db)
    a.run(MockInput(topic="并发", session_id="sess-A"))
    a.run(MockInput(topic="并发", session_id="sess-A", user_answer="答A"))

    # 新 session_id = 新面试：从第 1 题开始，turn_index 归零，不受 sess-A 影响。
    fresh = a.run(MockInput(topic="数据库", session_id="sess-B"))
    assert fresh.status == "active" and fresh.turn_index == 0 and fresh.question


# ----------------------------------------------------------------- 过期护栏（状态缺失）
def test_resume_with_lost_state_expires_gracefully(tmp_db: str):
    # 在 A 库开一场；用**另一个库**的 agent 续跑 → 读不到状态 → 优雅 expired，不崩。
    a = MockInterviewAgent(db_path=tmp_db)
    a.run(MockInput(topic="并发", session_id="sess-lost"))

    other_db = _fresh_db()
    b = MockInterviewAgent(db_path=other_db)                      # 不同库 ≈ 状态丢失
    out = b.run(MockInput(topic="并发", session_id="sess-lost", user_answer="继续"))
    assert out.status == "expired"                              # 优雅过期，而不是异常
    assert "重新开始" in (out.followup or "")


def test_answer_unknown_session_does_not_crash(tmp_db: str):
    m = MockInterviewAgent(db_path=tmp_db)
    out = m.run(MockInput(topic="", session_id="never-started", user_answer="hi"))
    assert out.status == "expired"


# ----------------------------------------------------------------- server 端过期处理
def test_server_expired_mock_clears_active(monkeypatch):
    from learnforge.app import server
    from learnforge.contracts.agents.mock import MockOutput
    out = MockOutput(session_id="s", status="expired",
                     followup="该模拟面试会话已过期或不存在，请重新开始一场面试。")
    body = server._mock_response(server._mgr(), out, "s")
    assert body["mock_active"] is False
    assert body["mock_session_id"] is None
    assert body.get("mock_expired") is True
    assert "重新开始" in body["reply_text"]
