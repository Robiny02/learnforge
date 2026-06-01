import json

from learnforge.mcp.registry import MCP_REGISTRY
from learnforge.tools.mcp.config import load_descriptors
from learnforge.tools.mcp.servers import web


def test_builtin_mcp_descriptors_are_opt_in(monkeypatch):
    monkeypatch.delenv("LF_MCP_SERVERS", raising=False)
    monkeypatch.delenv("LF_ENABLE_BUILTIN_MCP", raising=False)
    assert load_descriptors() == []

    monkeypatch.setenv("LF_ENABLE_BUILTIN_MCP", "1")
    descriptors = load_descriptors()
    assert [d.name for d in descriptors] == ["notion", "web", "github"]
    assert all(d.transport == "stdio" for d in descriptors)


def test_builtin_mcp_tools_are_registered_for_skills():
    for name in [
        "mcp.notion.search_pages",
        "mcp.notion.read_page",
        "mcp.notion.create_learning_note",
        "mcp.web.fetch_url",
        "mcp.github.repo_summary",
        "mcp.github.list_tree",
        "mcp.github.read_file",
    ]:
        spec = MCP_REGISTRY.get(name)
        assert spec is not None
        assert spec.effect.value == "external"
        assert spec.audit_required


def test_web_fetch_rejects_non_http_urls():
    result = web.fetch_url({"url": "file:///etc/passwd"})
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert "http://" in payload["error"]
