"""受控文件工具的权限边界 + 统一只读 EvidenceResearchAgent 扩展点。

覆盖（全部离线，无需 API key）：
- file.read/write/edit 已注册，effect/owner/audit 正确；注册表里没有 exec/shell。
- DiagnosisAgent 不能调 file.write/file.edit（也没有 exec 工具可调）。
- 未授权 agent 调 file.write/file.edit 被 require_tool 拒绝。
- Manager 能看到 file registry，但 write/edit 默认安全：handler 直调也只 dry-run，不真改文件。
- file.read 限定 workspace 只读根，越界路径被拒。
- EvidenceResearchAgent 默认只读（skill 不声明任何写/编辑工具）。
- EvidenceResearchAgent 输出 EvidencePacket（片段 + 指针），不回灌完整文件内容。
- Manager.gather_evidence 拿到 packet，并能作为只读 artifact 注入 diagnosis context。
"""

import pytest

from learnforge import config
from learnforge.agents.evidence import EvidenceResearchAgent
from learnforge.agents.diagnosis import DiagnosisAgent
from learnforge.agents.qa.qa_agent import QAAgent
from learnforge.contracts.agents.evidence import EvidencePacket, EvidenceRequest
from learnforge.contracts.enums import EvidenceSourceType
from learnforge.mcp.base import ToolEffect
from learnforge.mcp.registry import MCP_REGISTRY
from learnforge.skills.base import SkillPermissionError
from learnforge.tools.collection import TOOLS
from learnforge.orchestration.manager import ManagerAgent


# --------------------------------------------------------------- registry shape
def test_file_tools_registered_with_correct_effects():
    assert MCP_REGISTRY.is_known("file.read")
    assert MCP_REGISTRY.is_known("file.write")
    assert MCP_REGISTRY.is_known("file.edit")
    assert MCP_REGISTRY.spec_for("file.read").effect.value == ToolEffect.READ.value
    assert MCP_REGISTRY.spec_for("file.write").effect.value == ToolEffect.WRITE.value
    assert MCP_REGISTRY.spec_for("file.edit").effect.value == ToolEffect.WRITE.value


def test_file_write_edit_are_audited_and_owned():
    for name in ("file.write", "file.edit"):
        spec = MCP_REGISTRY.spec_for(name)
        assert spec.audit_required is True
        assert spec.owner_agents  # 必须有 owner（不可被任意 agent 声明）


def test_no_shell_or_exec_capability_exists():
    # 没有任何 exec/shell 类工具或 namespace。
    for name in ("exec", "shell", "bash", "shell.exec", "process.run", "exec.run"):
        assert not MCP_REGISTRY.is_known(name), f"{name} must not exist this phase"
    # file.* 是 namespace（前缀查询会解析），但任意 file.exec 既不可声明也无 handler → 不可调用。
    assert not MCP_REGISTRY.is_known("file.exec", for_declaration=True)
    assert not TOOLS.has_handler("file.exec")


def test_evidence_source_tools_registered():
    for name in ("repo.search", "attachment.recall", "resume.recall", "agent.evidence"):
        assert MCP_REGISTRY.is_known(name)


# ------------------------------------------------------- diagnosis cannot write
def test_diagnosis_cannot_use_file_write_or_edit(tmp_db):
    diag = DiagnosisAgent(db_path=tmp_db)
    for name in ("file.write", "file.edit"):
        with pytest.raises(SkillPermissionError):
            diag.require_tool(name)


def test_diagnosis_has_no_exec_tool_to_call(tmp_db):
    diag = DiagnosisAgent(db_path=tmp_db)
    # 没有 exec/shell 工具，连声明都不可能；require_tool 直接拒绝。
    with pytest.raises(SkillPermissionError):
        diag.require_tool("exec")


# ----------------------------------------------- unauthorized agents are denied
def test_unauthorized_agent_file_write_is_denied(tmp_db):
    qa = QAAgent(db_path=tmp_db)
    with pytest.raises(SkillPermissionError):
        qa.require_tool("file.write")
    with pytest.raises(SkillPermissionError):
        qa.call_tool("file.edit", {"path": "x", "old_string": "a", "new_string": "b"})


# ----------------------------------- manager sees registry but writes stay safe
def test_manager_sees_file_registry_but_cannot_grant_write(tmp_db):
    mgr = ManagerAgent(db_path=tmp_db)
    assert MCP_REGISTRY.is_known("file.write")  # registry 可见
    with pytest.raises(SkillPermissionError):    # 但 Manager skill 未声明 → 不能调
        mgr.require_tool("file.write")


def test_file_write_handler_is_dry_run_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_WRITE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "FILE_WRITE_ENABLED", False)
    target = tmp_path / "out.txt"
    res = TOOLS.call_result("file.write", {"path": "out.txt", "content": "hello world"})
    assert res.ok
    assert res.data["dry_run"] is True
    assert "hello world" in res.data["diff"]
    assert not target.exists()  # 默认绝不真实落盘


def test_file_write_real_write_requires_explicit_enable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_WRITE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "FILE_WRITE_ENABLED", True)
    target = tmp_path / "out.txt"
    res = TOOLS.call_result("file.write", {"path": "out.txt", "content": "real"})
    assert res.ok and res.data["dry_run"] is False
    assert target.read_text() == "real"


# ------------------------------------------------ file.read workspace boundary
def test_file_read_within_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_READ_ROOT", str(tmp_path))
    (tmp_path / "note.md").write_text("evidence content", encoding="utf-8")
    res = TOOLS.call_result("file.read", {"path": "note.md"})
    assert res.ok and res.data["content"] == "evidence content"


def test_file_read_rejects_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORKSPACE_READ_ROOT", str(tmp_path))
    res = TOOLS.call_result("file.read", {"path": "../../../../etc/passwd"})
    assert not res.ok
    assert res.error == "path_out_of_workspace"


# --------------------------------------------------- evidence agent: read-only
def test_evidence_agent_declares_no_write_tools(tmp_db):
    ev = EvidenceResearchAgent(db_path=tmp_db)
    writes = [t for t in ev.skill.spec.allowed_tools
              if t.startswith("repository.write") or t in ("file.write", "file.edit")]
    assert writes == []
    for name in ("file.write", "file.edit", "repository.write.mastery"):
        with pytest.raises(SkillPermissionError):
            ev.require_tool(name)


def test_evidence_agent_returns_packet_with_refs_not_raw_dump(tmp_path, monkeypatch, tmp_db):
    monkeypatch.setattr(config, "WORKSPACE_READ_ROOT", str(tmp_path))
    big = "SECRET_TOKEN " + ("x" * 5000)  # 大文件：验证不整文件回灌
    (tmp_path / "report.md").write_text(big, encoding="utf-8")

    ev = EvidenceResearchAgent(db_path=tmp_db)
    packet = ev.run(EvidenceRequest(
        query="token", source_types=[EvidenceSourceType.FILE], targets=["report.md"]))

    assert isinstance(packet, EvidencePacket)
    assert packet.evidence_refs, "should collect at least one evidence ref"
    ref = packet.evidence_refs[0]
    assert ref.locator == "report.md"
    assert len(ref.snippet) < len(big)  # 片段，不是整文件
    # 隔离：完整 5000 字符正文不应出现在对外 packet 的任何文本里。
    assert big not in packet.summary
    assert all(big not in r.snippet for r in packet.evidence_refs)
    assert any(s.source_type == EvidenceSourceType.FILE for s in packet.source_refs)


def test_evidence_agent_missing_info_when_nothing_found(tmp_db):
    ev = EvidenceResearchAgent(db_path=tmp_db)
    packet = ev.run(EvidenceRequest(
        query="nonexistent-xyz", source_types=[EvidenceSourceType.RESUME]))
    assert packet.confidence == 0.0
    assert packet.missing_info


# --------------------------------------- manager gathers evidence for diagnosis
def test_manager_gather_evidence_and_attach_to_diagnosis_context(tmp_path, monkeypatch, tmp_db):
    monkeypatch.setattr(config, "WORKSPACE_READ_ROOT", str(tmp_path))
    (tmp_path / "proj.md").write_text("uses Redis caching and FTS5", encoding="utf-8")

    mgr = ManagerAgent(db_path=tmp_db)
    packet = mgr.gather_evidence(EvidenceRequest(
        query="Redis", source_types=[EvidenceSourceType.FILE], targets=["proj.md"]))
    assert isinstance(packet, EvidencePacket)
    assert packet.evidence_refs

    context = {}
    artifact = mgr.attach_evidence_to_context(packet, context)
    assert context["evidence_artifact"] == artifact
    assert "proj.md" in artifact  # 指针在
    assert isinstance(artifact, str) and artifact  # 只读字符串 artifact

    # Diagnosis 仍然只读（拿到 artifact 不改任何 state）。
    with pytest.raises(SkillPermissionError):
        mgr.diagnosis.require_tool("repository.write.mastery")
