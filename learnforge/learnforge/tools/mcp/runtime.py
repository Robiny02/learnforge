"""极简 MCP stdio 客户端（不依赖官方 mcp SDK，Python 3.9 可用）。

MCP stdio 传输 = 换行分隔的 JSON-RPC 2.0 消息（每行一条）。本客户端只实现够用的子集：
initialize → notifications/initialized → tools/list → tools/call。server 进程可用任意解释器。
所有失败都不抛到上层（返回 None / 错误 dict），由调用方降级。
"""

from __future__ import annotations

import json
import os
import select
import subprocess
from typing import Any, Dict, List, Optional

from .config import McpServerDescriptor

_PROTOCOL = "2024-11-05"


class McpClientRuntime:
    """一个外部 MCP server 的客户端会话（stdio）。"""

    def __init__(self, descriptor: McpServerDescriptor) -> None:
        self.descriptor = descriptor
        self._proc: Optional[subprocess.Popen] = None
        self._id = 0
        self.connected = False

    # --- 低层 IO ---
    def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _readline(self, timeout: float) -> Optional[str]:
        assert self._proc and self._proc.stdout
        r, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not r:
            return None
        return self._proc.stdout.readline()

    def _request(self, method: str, params: Optional[dict] = None, timeout: float = 20.0) -> Optional[dict]:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline_reads = 200
        while deadline_reads > 0:
            line = self._readline(timeout)
            if line is None:
                return None
            line = line.strip()
            if not line:
                deadline_reads -= 1
                continue
            try:
                msg = json.loads(line)
            except Exception:
                deadline_reads -= 1
                continue  # 跳过非 JSON 行（部分 server 的日志）
            if msg.get("id") == rid:
                return msg
            deadline_reads -= 1  # 跳过 notification / 其它 id
        return None

    # --- 生命周期 ---
    def connect(self, timeout: float = 20.0) -> bool:
        d = self.descriptor
        if d.transport != "stdio" or not d.command:
            return False  # 目前只实现 stdio（sse/http 见 runtime_factory 占位）
        try:
            env = {**os.environ, **d.env}
            self._proc = subprocess.Popen(
                [d.command, *d.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, env=env,
            )
            resp = self._request("initialize", {
                "protocolVersion": _PROTOCOL, "capabilities": {},
                "clientInfo": {"name": "learnforge", "version": "0.1"},
            }, timeout=timeout)
            if not resp or "result" not in resp:
                self.close()
                return False
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.connected = True
            return True
        except Exception:
            self.close()
            return False

    def list_tools(self) -> List[Dict[str, Any]]:
        if not self.connected:
            return []
        resp = self._request("tools/list")
        if not resp or "result" not in resp:
            return []
        return list(resp["result"].get("tools") or [])

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> Dict[str, Any]:
        if not self.connected:
            return {"error": "mcp client not connected"}
        resp = self._request("tools/call", {"name": name, "arguments": arguments or {}}, timeout=60.0)
        if not resp:
            return {"error": "no response from mcp server"}
        if "error" in resp:
            return {"error": str(resp["error"])[:300]}
        result = resp.get("result") or {}
        # MCP tool 结果在 content: [{type:text,text:..}, ...]
        texts = [c.get("text", "") for c in (result.get("content") or []) if c.get("type") == "text"]
        return {"ok": not result.get("isError"), "observation": "\n".join(texts)[:4000], "raw": result}

    def close(self) -> None:
        self.connected = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
