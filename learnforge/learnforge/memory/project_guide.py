"""Project Guide 层加载器（固定 Prompt Stack 的第二层）。

Project Guide = 项目级**运行时**约束（架构/硬不变量/输出规范）——类似 CLAUDE.md 之于开发者，但这是给
agent 看的精简版（不含运行命令/测试细节）。真值文件 = `learnforge/PROJECT_GUIDE.md`，整段加载、缓存、
不索引、不衰减（与 User Memory 的 MEMORY.md 同思路，但维度是"项目"而非"用户"）。

缺失/读失败 → 空串（链路永远通，prefix 少一层而已）。`reset_cache()` 供测试。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def _guide_path() -> Path:
    # learnforge/learnforge/PROJECT_GUIDE.md —— 与包同目录，随包分发。
    return Path(__file__).resolve().parents[1] / "PROJECT_GUIDE.md"


@lru_cache(maxsize=1)
def _cached_guide() -> str:
    try:
        p = _guide_path()
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""


def project_guide_text() -> str:
    """返回 Project Guide 文本（缓存）。缺失 → 空串。"""
    return _cached_guide()


def reset_cache() -> None:
    """清缓存（测试用，便于改文件后重读）。"""
    _cached_guide.cache_clear()
