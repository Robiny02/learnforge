"""Claude-like subagent capability boundary tests.

这些测试关注 skill 声明的权限是否真正参与运行期决策，而不是只作为文档存在。
"""

import pytest

from learnforge.agents.diagnosis import DiagnosisAgent
from learnforge.contracts.agents.diagnosis import DiagnosisInput
from learnforge.agents.qa.qa_agent import QAAgent
from learnforge.contracts.agents.qa import QAInput, RouterOutput, SynthesizerOutput
from learnforge.contracts.enums import AgentId, ModelTier, QType
from learnforge.mcp.base import ToolEffect
from learnforge.mcp.registry import MCP_REGISTRY
from learnforge.skills.bootstrap import ensure_skills_registered
from learnforge.skills.base import Skill, SkillPermissionError, SkillSpec
from learnforge.skills.registry import (
    SKILL_REGISTRY,
    SkillRegistry,
    ToolNotAllowedError,
    UnknownToolError,
)

# 只读 agent：绝不应在 skill 中声明任何 repository.write.* 权限。
READ_ONLY_AGENTS = [
    AgentId.DIAGNOSIS,
    AgentId.RETRIEVAL,
    AgentId.ROUTER,
    AgentId.SYNTHESIZER,
    AgentId.VERIFIER,
    AgentId.JUDGE,
    AgentId.STRATEGIST,
    AgentId.COACH,
]


def test_default_qa_shell_has_subagent_and_retrieval_permissions(tmp_db):
    qa = QAAgent(db_path=tmp_db)

    assert qa.has_tool("agent.router")
    assert qa.has_tool("agent.synthesizer")
    assert qa.has_tool("agent.verifier")
    assert qa.has_tool("retrieval.search")
    assert not qa.has_tool("repository.write.mastery")


def test_missing_retrieval_permission_blocks_retrieval_boundary_case(tmp_db):
    qa = QAAgent(db_path=tmp_db)
    qa.skill = Skill(
        SkillSpec(
            name="qa.no_retrieval.test",
            agent_id=AgentId.QA,
            model_tier=ModelTier.HAIKU,
            allowed_tools=["agent.router", "agent.synthesizer", "agent.verifier"],
        )
    )
    qa.router.run = lambda payload: RouterOutput(
        q_type=QType.CONCEPT,
        need_retrieval=True,
        need_verifier=False,
    )

    with pytest.raises(SkillPermissionError):
        qa.run(QAInput(question="解释一下乐观锁"))


def test_default_skills_have_progressive_loading_sections():
    ensure_skills_registered()
    for agent_id in AgentId:
        skill = SKILL_REGISTRY.primary(agent_id)
        assert skill is not None, f"{agent_id} should have a default skill"
        assert skill.spec.frontmatter
        assert skill.spec.tool_descriptions
        assert skill.spec.bash
        assert skill.workflow()
        assert skill.spec.constraints

        brief = skill.load_instructions()
        assert "Front Matter:" in brief
        assert "Constraints:" in brief
        assert "Workflow:" in brief
        assert "Tools:" not in brief  # tools load progressively, not by default
        assert "Tools:" in skill.load_instructions(["tools"])


def test_diagnosis_skill_trigger_metadata_selects_progressively():
    ensure_skills_registered()

    by_intent = SKILL_REGISTRY.select(AgentId.DIAGNOSIS, intent="diagnose")
    by_event = SKILL_REGISTRY.select(AgentId.DIAGNOSIS, event="mock_settled")
    by_keyword = SKILL_REGISTRY.select(AgentId.DIAGNOSIS, text="我哪里比较薄弱")

    assert by_intent[0].spec.name == "diagnosis.v1"
    assert by_event[0].spec.name == "diagnosis.v1"
    assert by_keyword[0].spec.name == "diagnosis.v1"
    # SOP 现在经 "sop" 段被默认加载链激活（原 "react_sop" 键从未被加载）。
    assert "Diagnosis ReAct SOP" in by_intent[0].load_instructions()


def test_diagnosis_react_uses_real_tool_calls(seeded_db):
    diag = DiagnosisAgent(db_path=seeded_db)
    diag.run(DiagnosisInput())

    allowed_tools = [a for a in diag.permission_audit if a["allowed"]]
    names = [a["tool"] for a in allowed_tools]
    assert "diagnosis.search_events" in names
    assert "diagnosis.get_mastery_snapshot" in names


@pytest.mark.parametrize("agent_id", READ_ONLY_AGENTS)
def test_read_only_agents_declare_no_write_tools(agent_id):
    ensure_skills_registered()
    skill = SKILL_REGISTRY.primary(agent_id)
    assert skill is not None
    writes = [t for t in skill.spec.allowed_tools if t.startswith("repository.write")]
    assert writes == [], f"{agent_id} must be read-only but declares writes: {writes}"


def test_diagnosis_cannot_require_write_mastery(tmp_db):
    diag = DiagnosisAgent(db_path=tmp_db)
    with pytest.raises(SkillPermissionError):
        diag.require_tool("repository.write.mastery")


def test_all_default_skill_tools_are_registered():
    ensure_skills_registered()
    for agent_id in AgentId:
        skill = SKILL_REGISTRY.primary(agent_id)
        assert skill is not None
        for tool in skill.spec.allowed_tools:
            assert MCP_REGISTRY.is_known(tool), f"{agent_id}: unknown tool '{tool}'"


def test_registering_skill_with_unknown_tool_is_rejected():
    reg = SkillRegistry()
    with pytest.raises(UnknownToolError):
        reg.register_spec(
            SkillSpec(
                name="bogus.test",
                agent_id=AgentId.QA,
                model_tier=ModelTier.HAIKU,
                allowed_tools=["totally.unknown.tool"],
            )
        )


def test_registering_skill_with_tool_owned_by_another_agent_is_rejected():
    reg = SkillRegistry()
    with pytest.raises(ToolNotAllowedError):
        reg.register_spec(
            SkillSpec(
                name="qa.write.test",
                agent_id=AgentId.QA,
                model_tier=ModelTier.HAIKU,
                allowed_tools=["repository.write.mastery"],
            )
        )


def test_broad_write_namespace_is_not_declarable():
    reg = SkillRegistry()
    with pytest.raises(UnknownToolError):
        reg.register_spec(
            SkillSpec(
                name="manager.broad_write.test",
                agent_id=AgentId.MANAGER,
                model_tier=ModelTier.SONNET,
                allowed_tools=["repository.write"],
            )
        )


def test_write_capabilities_have_owners_and_audit_enabled():
    write_tools = MCP_REGISTRY.write_tools()
    assert write_tools
    for spec in write_tools:
        if spec.is_namespace:
            continue
        assert spec.effect.value == ToolEffect.WRITE.value
        assert spec.owner_agents
        assert spec.audit_required


def test_strategy_tools_must_be_allowed_tools():
    ensure_skills_registered()
    for agent_id in AgentId:
        skill = SKILL_REGISTRY.primary(agent_id)
        assert skill is not None
        for name, rule in skill.spec.strategy_rules.items():
            missing = [t for t in rule.get("tools", []) if t not in skill.spec.allowed_tools]
            assert missing == [], f"{agent_id}:{name} references unallowed tools {missing}"


def test_qa_skill_strategy_can_skip_retrieval_even_if_router_switch_is_true(tmp_db):
    qa = QAAgent(db_path=tmp_db)
    qa.router.run = lambda payload: RouterOutput(
        q_type=QType.CHITCHAT,
        need_retrieval=True,
        need_verifier=True,
    )
    qa.retrieval.run = lambda payload: pytest.fail("chitchat strategy should skip retrieval")
    qa.verifier.run = lambda payload: pytest.fail("chitchat strategy should skip verifier")
    qa.synthesizer.run = lambda payload: SynthesizerOutput(draft="hello", claims=[])

    out = qa.run(QAInput(question="hi"))

    assert out.answer == "hello"
    assert out.topic == "chitchat"


def test_permission_denial_is_audited(tmp_db):
    diag = DiagnosisAgent(db_path=tmp_db)
    with pytest.raises(SkillPermissionError):
        diag.require_tool("repository.write.mastery")

    assert diag.permission_audit[-1]["tool"] == "repository.write.mastery"
    assert diag.permission_audit[-1]["allowed"] is False
    assert diag.permission_audit[-1]["effect"] == "write"
    assert diag.permission_audit[-1]["audit_required"] is True
