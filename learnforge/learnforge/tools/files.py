"""受控文件工具 handler —— file.read / file.write / file.edit。

权限边界由 skill.allowed_tools + BaseAgent.require_tool 把守（注册 ≠ 授权）。这里是**第二道闸**：
即便某 handler 被直接调用，也强约束：

  - file.read  : 只读，路径必须落在 `config.WORKSPACE_READ_ROOT` 内（默认 = 仓库根）。
  - file.write : **默认 dry-run** —— 只算 diff/preview，不落盘。仅当 `config.FILE_WRITE_ENABLED`
                 且目标在可写沙箱 `config.WORKSPACE_WRITE_DIR` 内时才真实写。
  - file.edit  : 同 write，按 old_string→new_string 精确替换，默认 dry-run 出 diff。

本期**不引入** shell/exec：本模块只读/受控写文本，没有任何执行子进程的能力。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Dict

from .. import config
from .base import ToolResult

_MAX_READ_BYTES = 200_000  # 单次读取上限，避免把超大文件灌进上下文


def register_file_tools(registry: Any) -> None:
    """注册 file.read / file.write / file.edit 的真 handler 到 TOOLS（经 runtime facade）。"""
    registry.register("file.read", file_read)
    registry.register("file.write", file_write)
    registry.register("file.edit", file_edit)


# --------------------------------------------------------------------- helpers
def _read_root() -> Path:
    return Path(config.WORKSPACE_READ_ROOT).resolve()


def _write_root() -> Path:
    return Path(config.WORKSPACE_WRITE_DIR).resolve()


def _contained(path: Path, root: Path) -> bool:
    """path 是否在 root 内（已 resolve，防 `..` 越界 / 符号链接逃逸）。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve(path_str: str, root: Path) -> Path:
    base = Path(path_str)
    return (base if base.is_absolute() else (root / base)).resolve()


def _unified_diff(before: str, after: str, path: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    )
    return "".join(diff)


# ------------------------------------------------------------------- file.read
def file_read(args: Dict[str, Any]) -> ToolResult:
    raw = str(args.get("path") or "").strip()
    if not raw:
        return ToolResult(name="file.read", ok=False, observation="missing 'path'",
                          error="missing_path")
    root = _read_root()
    target = _resolve(raw, root)
    if not _contained(target, root):
        return ToolResult(name="file.read", ok=False,
                          observation=f"path escapes workspace read root: {raw}",
                          error="path_out_of_workspace")
    if not target.is_file():
        return ToolResult(name="file.read", ok=False, observation=f"not a file: {raw}",
                          error="not_a_file")
    try:
        data = target.read_bytes()[:_MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(name="file.read", ok=False, observation=str(exc), error="read_error")
    truncated = target.stat().st_size > _MAX_READ_BYTES
    return ToolResult(
        name="file.read", ok=True,
        observation=f"read {len(text)} chars from {raw}" + (" (truncated)" if truncated else ""),
        data={"path": str(target), "content": text, "truncated": truncated},
    )


# ------------------------------------------------------------------ file.write
def file_write(args: Dict[str, Any]) -> ToolResult:
    raw = str(args.get("path") or "").strip()
    new_content = str(args.get("content") or "")
    if not raw:
        return ToolResult(name="file.write", ok=False, observation="missing 'path'",
                          error="missing_path")
    root = _write_root()
    target = _resolve(raw, root)
    before = target.read_text(encoding="utf-8") if target.is_file() else ""
    diff = _unified_diff(before, new_content, raw)
    return _apply_or_preview("file.write", target, root, before, new_content, diff,
                             bool(args.get("dry_run", False)))


# ------------------------------------------------------------------- file.edit
def file_edit(args: Dict[str, Any]) -> ToolResult:
    raw = str(args.get("path") or "").strip()
    old_string = str(args.get("old_string") or "")
    new_string = str(args.get("new_string") or "")
    if not raw:
        return ToolResult(name="file.edit", ok=False, observation="missing 'path'",
                          error="missing_path")
    root = _write_root()
    target = _resolve(raw, root)
    if not target.is_file():
        return ToolResult(name="file.edit", ok=False, observation=f"not a file: {raw}",
                          error="not_a_file")
    before = target.read_text(encoding="utf-8")
    if old_string and old_string not in before:
        return ToolResult(name="file.edit", ok=False,
                          observation="old_string not found in file", error="no_match")
    after = before.replace(old_string, new_string) if old_string else before + new_string
    diff = _unified_diff(before, after, raw)
    return _apply_or_preview("file.edit", target, root, before, after, diff,
                             bool(args.get("dry_run", False)))


def _apply_or_preview(name: str, target: Path, root: Path, before: str, after: str,
                      diff: str, force_dry_run: bool) -> ToolResult:
    """统一的写/dry-run 决策：默认 dry-run；仅 FILE_WRITE_ENABLED+沙箱内+未强制 dry-run 才真写。"""
    in_sandbox = _contained(target, root)
    may_write = config.FILE_WRITE_ENABLED and in_sandbox and not force_dry_run
    if not may_write:
        reason = ("forced dry_run" if force_dry_run
                  else "writes disabled (set LF_FILE_WRITE_ENABLED=1)" if not config.FILE_WRITE_ENABLED
                  else "path outside writable sandbox")
        return ToolResult(
            name=name, ok=True,
            observation=f"dry-run ({reason}); no file changed",
            data={"dry_run": True, "would_write_path": str(target), "diff": diff,
                  "reason": reason, "in_sandbox": in_sandbox},
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
    except OSError as exc:
        return ToolResult(name=name, ok=False, observation=str(exc), error="write_error")
    return ToolResult(
        name=name, ok=True, observation=f"wrote {len(after)} chars to {target}",
        data={"dry_run": False, "path": str(target), "diff": diff},
    )
