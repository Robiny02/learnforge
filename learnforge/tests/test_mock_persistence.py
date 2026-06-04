"""Mock checkpoint 持久化（P0）：跨进程重启可 resume + 缺失会话优雅过期不崩。

锁住：
- 默认 SqliteSaver 把图状态落盘：新建 agent 实例（≈进程重启）能继续上一场面试。
- 新 session_id = 开新面试，与旧会话互不干扰。
- checkpoint 缺失（重启后用 MemorySaver / 会话不存在）→ answer 返回 status=expired，不抛 KeyError。
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
def test_interview_resumes_after_restart(tmp_db: str, monkeypatch):
    ckpt = os.path.join(tempfile.mkdtemp(), "mock_ckpt.db")
    monkeypatch.delenv("LF_MOCK_CHECKPOINT", raising=False)        # 关掉测试默认的 memory
    monkeypatch.setenv("LF_MOCK_CHECKPOINT_DB", ckpt)             # 指定落盘 checkpoint 库
    sid = "sess-persist"

    # 进程 A：开场 + 答一轮，停在下一题（active）。
    a = MockInterviewAgent(db_path=tmp_db)
    assert a.persistent is True                                   # 确认走了 SqliteSaver
    a.run(MockInput(topic="并发", session_id=sid))
    o = a.run(MockInput(topic="并发", session_id=sid, user_answer="我用乐观锁加版本号"))
    assert o.status == "active" and o.turn_index == 1

    # 进程 B：全新实例（≈重启）。同一 checkpoint 库 → 能看到中断点并继续作答。
    b = MockInterviewAgent(db_path=tmp_db)
    assert b._has_resumable({"configurable": {"thread_id": sid}}) is True
    o2 = b.run(MockInput(topic="并发", session_id=sid, user_answer="补充：CAS 自旋重试"))
    assert o2.status == "active"
    assert o2.turn_index == 2                                     # 在上一场基础上继续推进
    assert o2.question                                           # 给出了下一题


def test_new_session_is_independent(tmp_db: str, monkeypatch):
    ckpt = os.path.join(tempfile.mkdtemp(), "mock_ckpt.db")
    monkeypatch.delenv("LF_MOCK_CHECKPOINT", raising=False)
    monkeypatch.setenv("LF_MOCK_CHECKPOINT_DB", ckpt)
    a = MockInterviewAgent(db_path=tmp_db)
    a.run(MockInput(topic="并发", session_id="sess-A"))
    a.run(MockInput(topic="并发", session_id="sess-A", user_answer="答A"))

    # 新 session_id = 新面试：从第 1 题开始，turn_index 归零，不受 sess-A 影响。
    fresh = a.run(MockInput(topic="数据库", session_id="sess-B"))
    assert fresh.status == "active" and fresh.turn_index == 0 and fresh.question


# ----------------------------------------------------------------- 过期护栏（无 checkpoint）
def test_resume_without_checkpoint_expires_gracefully(monkeypatch):
    # 用测试默认 memory checkpointer：新 agent 看不到旧会话 → 不应崩，应 status=expired。
    monkeypatch.setenv("LF_MOCK_CHECKPOINT", "memory")
    a = MockInterviewAgent()
    a.run(MockInput(topic="并发", session_id="sess-lost"))

    b = MockInterviewAgent()                                     # 新 MemorySaver（≈丢了 checkpoint）
    out = b.run(MockInput(topic="并发", session_id="sess-lost", user_answer="继续"))
    assert out.status == "expired"                              # 优雅过期，而不是 KeyError
    assert "重新开始" in (out.followup or "")


def test_answer_unknown_session_does_not_crash(monkeypatch):
    monkeypatch.setenv("LF_MOCK_CHECKPOINT", "memory")
    m = MockInterviewAgent()
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
