"""主图端到端冒烟（Design §2b）——compile + 四类意图 START→END。"""

from learnforge.graph.main_graph import compile_main_graph


def _invoke(app, user_input, monkeypatch, tmp_path):
    # 切到临时 cwd，避免默认 learnforge.db 落到包目录。
    monkeypatch.chdir(tmp_path)
    return app.invoke({
        "user_input": user_input, "session_id": "s", "trace_id": "t", "replan_count": 0,
    })


def test_compile():
    assert compile_main_graph() is not None


def test_single_intents(monkeypatch, tmp_path):
    app = compile_main_graph()
    for q, expect_agent in [
        ("乐观锁还是悲观锁?", "qa"),
        ("帮我生成学习计划", "planning"),
        ("诊断我的弱点", "diagnosis"),
    ]:
        r = _invoke(app, q, monkeypatch, tmp_path)
        assert r.get("reply_text")
        assert r.get("status")
        assert r["plan"][0]["agent"] == expect_agent


def test_composite_intent(monkeypatch, tmp_path):
    app = compile_main_graph()
    r = _invoke(app, "快面试了帮我准备一下", monkeypatch, tmp_path)
    assert [t["agent"] for t in r["plan"]] == ["diagnosis", "planning"]
    assert r.get("next_actions")  # 建议 mock
