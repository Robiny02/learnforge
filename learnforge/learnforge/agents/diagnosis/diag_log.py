"""Diagnose 行为日志 —— 每次诊断「做了什么」的可观测落盘记录。

与 `memory/log.py`（按轮的记忆读/写面板）不同，本模块**专门记录 DiagnosisAgent 每一次调用的行为**：
是弱点诊断还是简历/项目诊断、走 LLM ReAct 还是确定性兜底、读了哪些仓库/文件、ReAct 追读了几轮、
子断言支持度分布、结果是否落库可召回、耗时多少。用户需求：「单独用一个日志记录 diagnose 每次的行为」。

设计取舍（保持简洁、零重依赖）：
- **持久化为 JSONL**：每次诊断追加一行 JSON（一条记录 = 一次调用），跨进程/重启不丢，便于事后审计与评估。
  路径：环境变量 `LF_DIAGNOSE_LOG` 覆盖，默认 `<package_root>/logs/diagnose.jsonl`。
- 进程级单例 `DIAG_LOG`，同时在内存保留 `last` / `entries`，供测试与 CLI 即时读取。
- 写日志 best-effort：任何 IO 失败都不得影响诊断主流程（诊断本身严格只读，日志更不能反噬）。
- 不 import 存储/agent，避免循环导入；渲染只产纯文本，面板留给上层。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _default_log_path() -> Path:
    env = os.environ.get("LF_DIAGNOSE_LOG")
    if env:
        return Path(env)
    # learnforge/agents/diagnosis/diag_log.py → 包根 learnforge/ 下的 logs/
    pkg_root = Path(__file__).resolve().parents[2]
    return pkg_root / "logs" / "diagnose.jsonl"


@dataclass
class DiagnoseRecord:
    """一次诊断调用的行为快照。`extra` 容纳各 kind 专有字段。"""

    kind: str                         # "weakness" | "resume"
    summary: str = ""                 # 一句话人读摘要
    react_triggered: bool = False     # 是否真正触发了 ReAct（弱点：LLM 自主；简历：repo 受控追读）
    react_rounds: int = 0             # ReAct 追读/步数（简历=re-search 轮，弱点=ReAct step 数）
    degraded: bool = False            # 是否走了降级/确定性兜底
    persisted_ref: Optional[str] = None  # 落库可召回的引用（简历诊断 chunk_id）；None=未落库
    latency_ms: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts, "kind": self.kind, "summary": self.summary,
            "react_triggered": self.react_triggered, "react_rounds": self.react_rounds,
            "degraded": self.degraded, "persisted_ref": self.persisted_ref,
            "latency_ms": self.latency_ms,
        }
        d.update(self.extra)
        return d

    def render(self) -> str:
        flags = []
        flags.append("ReAct×%d" % self.react_rounds if self.react_triggered else "no-ReAct")
        if self.degraded:
            flags.append("degraded")
        flags.append("persisted" if self.persisted_ref else "not-persisted")
        if self.latency_ms is not None:
            flags.append(f"{self.latency_ms}ms")
        head = self.ts[11:19] if len(self.ts) >= 19 else self.ts
        return f"{head} [{self.kind}] {self.summary} ｜ {' / '.join(flags)}"


class DiagnoseLog:
    """diagnose 行为日志：`record()` 落一条（JSONL 追加 + 内存保留），`render_lines()` 渲染。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or _default_log_path()
        self._entries: List[DiagnoseRecord] = []
        self.enabled: bool = True

    @property
    def path(self) -> Path:
        return self._path

    @property
    def last(self) -> Optional[DiagnoseRecord]:
        return self._entries[-1] if self._entries else None

    @property
    def entries(self) -> List[DiagnoseRecord]:
        return list(self._entries)

    def record(self, rec: DiagnoseRecord) -> DiagnoseRecord:
        """追加一条诊断行为记录：写 JSONL + 内存保留。IO 失败不抛（best-effort）。"""
        if not self.enabled:
            return rec
        self._entries.append(rec)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec.as_dict(), ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - 日志失败绝不影响诊断主流程
            pass
        return rec

    def render_lines(self, limit: int = 20) -> List[str]:
        if not self._entries:
            return ["（暂无 diagnose 行为记录）"]
        return [e.render() for e in self._entries[-limit:]]

    def read_jsonl(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """从磁盘读回历史记录（跨进程审计/评估用）。文件缺失/损坏 → 跳过坏行。"""
        out: List[Dict[str, Any]] = []
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:  # noqa: BLE001 - 跳过损坏行
                        continue
        except FileNotFoundError:
            return []
        except Exception:  # noqa: BLE001
            return out
        return out[-limit:] if limit else out


# 进程级单例（与 LLM/EMBEDDINGS/MEMORY_LOG 同构）。
DIAG_LOG = DiagnoseLog()
