"""MCP server 配置：从 env `LF_MCP_SERVERS`(JSON 数组) 读取外部 server 描述。

例：
  LF_MCP_SERVERS='[{"name":"fetch","transport":"stdio",
                    "command":"/opt/homebrew/bin/python3.11","args":["-m","mcp_server_fetch"]}]'
未设置 → 返回 []（无外部 MCP，运行时只用本地工具）。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class McpServerDescriptor:
    name: str
    transport: str = "stdio"           # stdio | sse | http
    command: Optional[str] = None      # stdio: 可执行
    args: List[str] = field(default_factory=list)
    url: Optional[str] = None          # sse/http
    env: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True


def load_descriptors() -> List[McpServerDescriptor]:
    _load_local_env()
    out: List[McpServerDescriptor] = []
    if os.environ.get("LF_ENABLE_BUILTIN_MCP") in {"1", "true", "TRUE", "yes", "YES"}:
        out.extend(_builtin_descriptors())

    raw = os.environ.get("LF_MCP_SERVERS")
    if not raw:
        return out
    try:
        items = json.loads(raw)
    except Exception:
        return out
    for it in items if isinstance(items, list) else []:
        try:
            out.append(McpServerDescriptor(
                name=it["name"],
                transport=it.get("transport", "stdio"),
                command=it.get("command"),
                args=list(it.get("args") or []),
                url=it.get("url"),
                env=dict(it.get("env") or {}),
                enabled=bool(it.get("enabled", True)),
            ))
        except Exception:
            continue
    return [d for d in out if d.enabled]


def _load_local_env() -> None:
    """Load simple local .env files before deciding which MCP servers to boot."""

    seen: set[Path] = set()
    for base in [Path.cwd(), *Path(__file__).resolve().parents]:
        env_path = base / ".env"
        if env_path in seen or not env_path.exists():
            continue
        seen.add(env_path)
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


def _builtin_descriptors() -> List[McpServerDescriptor]:
    """Local stdio MCP servers that are useful for LearnForge workflows.

    They are opt-in to avoid surprising subprocesses during tests/imports. Each server
    reads credentials from the environment or a local .env file and degrades cleanly.
    """

    return [
        McpServerDescriptor(
            name="notion",
            transport="stdio",
            command=sys.executable,
            args=["-m", "learnforge.tools.mcp.servers.notion"],
        ),
        McpServerDescriptor(
            name="web",
            transport="stdio",
            command=sys.executable,
            args=["-m", "learnforge.tools.mcp.servers.web"],
        ),
        McpServerDescriptor(
            name="github",
            transport="stdio",
            command=sys.executable,
            args=["-m", "learnforge.tools.mcp.servers.github"],
        ),
    ]
