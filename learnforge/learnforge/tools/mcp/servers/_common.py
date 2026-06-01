"""Small JSON-RPC/MCP stdio server helpers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

ToolHandler = Callable[[Dict[str, Any]], Dict[str, Any]]

PROTOCOL_VERSION = "2024-11-05"


def load_local_env() -> None:
    """Load simple KEY=VALUE pairs from nearby .env files without logging secrets."""

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


def text_response(text: str, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def json_response(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    return text_response(json.dumps(payload, ensure_ascii=False, indent=2), is_error=is_error)


def _write(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(name: str, tools: Iterable[Dict[str, Any]], handlers: Dict[str, ToolHandler]) -> None:
    tool_list = list(tools)
    load_local_env()
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        req_id = req.get("id")
        method = req.get("method")
        if req_id is None and method == "notifications/initialized":
            continue
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": name, "version": "0.1.0"},
                }
            elif method == "tools/list":
                result = {"tools": tool_list}
            elif method == "tools/call":
                params = req.get("params") or {}
                tool_name = params.get("name")
                args = params.get("arguments") or {}
                handler = handlers.get(tool_name)
                if handler is None:
                    result = text_response(f"unknown tool: {tool_name}", is_error=True)
                else:
                    result = handler(args)
            else:
                _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "method not found"}})
                continue
            if req_id is not None:
                _write({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:  # noqa: BLE001
            if req_id is not None:
                _write({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": text_response(f"{type(exc).__name__}: {exc}", is_error=True),
                })


def schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}

