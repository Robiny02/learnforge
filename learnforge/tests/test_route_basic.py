"""基础路由测试：先验证最小 agent 分发是否正确。"""

from learnforge.orchestrator.manager import ManagerAgent
from learnforge.orchestrator.router import QARouter


def test_router_defaults_to_qa():
    router = QARouter()
    assert router.route("乐观锁还是悲观锁?").agent == "qa"


def test_router_routes_planning():
    router = QARouter()
    assert router.route("帮我制定学习计划").agent == "planning"


def test_router_routes_diagnosis():
    router = QARouter()
    assert router.route("帮我诊断薄弱点").agent == "diagnosis"


def test_router_routes_mock():
    router = QARouter()
    assert router.route("快面试了，帮我模拟面试").agent == "mock"


def test_manager_uses_router():
    mgr = ManagerAgent()
    assert mgr.decide("帮我制定学习计划").agent == "planning"
