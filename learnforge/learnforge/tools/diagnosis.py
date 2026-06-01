"""Diagnosis-oriented read/compute tools."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..contracts.agents.retrieval import RetrievalInput
from ..contracts.enums import KnowledgeScope, RetrievalMethod
from ..mastery import effective_mastery
from ..storage.repositories import (
    AtomRepository,
    EventRepository,
    MockTurnRepository,
    QAHistoryRepository,
)
from .base import ToolResult


def register_diagnosis_tools(registry: Any) -> None:
    registry.register("diagnosis.search_events", search_events)
    registry.register("diagnosis.get_mastery_snapshot", get_mastery_snapshot)
    registry.register("diagnosis.search_qa_history", search_qa_history)
    registry.register("diagnosis.search_mock_turns", search_mock_turns)
    registry.register("diagnosis.retrieve_knowledge", retrieve_knowledge)
    registry.register("diagnosis.analyze_code_static", analyze_code_static)


def search_events(args: Dict[str, Any]) -> ToolResult:
    db_path = _db_path(args)
    time_window = str(args.get("time_window") or "30d")
    focus_topics = set(_string_list(args.get("focus_topics")))
    events = EventRepository(db_path=db_path).list_window_dicts(time_window)
    if focus_topics:
        events = [e for e in events if e.get("topic") in focus_topics]
    return ToolResult(
        name="diagnosis.search_events",
        ok=True,
        observation=f"loaded {len(events)} interaction events from {time_window}",
        data={"events": events},
    )


def get_mastery_snapshot(args: Dict[str, Any]) -> ToolResult:
    db_path = _db_path(args)
    atom_ids = _string_list(args.get("atom_ids"))
    now = datetime.now(timezone.utc)
    repo = AtomRepository(db_path=db_path)
    atoms: Dict[str, Dict[str, Any]] = {}
    for atom_id in atom_ids:
        atom = repo.get(atom_id)
        if atom is None:
            continue
        atoms[atom_id] = {
            "atom_id": atom.atom_id,
            "topic": atom.topic,
            "title": atom.title,
            "mastery_score": atom.mastery_score,
            "effective_mastery": effective_mastery(
                atom.mastery_score, atom.decay_rate, atom.last_reviewed_at, now
            ),
            "mastery_tier": atom.mastery_tier.value,
            "last_reviewed_at": atom.last_reviewed_at.isoformat() if atom.last_reviewed_at else None,
        }
    return ToolResult(
        name="diagnosis.get_mastery_snapshot",
        ok=True,
        observation=f"joined mastery for {len(atoms)} of {len(atom_ids)} requested atoms",
        data={"atoms": atoms},
    )


def search_qa_history(args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "")
    top_k = _int(args.get("top_k"), 8)
    chunks = QAHistoryRepository(db_path=_db_path(args)).fts_match(query, top_k=top_k)
    return ToolResult(
        name="diagnosis.search_qa_history",
        ok=True,
        observation=f"found {len(chunks)} QA history hits",
        data={"chunks": [c.model_dump(mode="json") for c in chunks]},
    )


def search_mock_turns(args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "")
    top_k = _int(args.get("top_k"), 8)
    chunks = MockTurnRepository(db_path=_db_path(args)).fts_match(query, top_k=top_k)
    return ToolResult(
        name="diagnosis.search_mock_turns",
        ok=True,
        observation=f"found {len(chunks)} mock-turn hits",
        data={"chunks": [c.model_dump(mode="json") for c in chunks]},
    )


def retrieve_knowledge(args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "")
    scopes = [
        KnowledgeScope(s)
        for s in _string_list(args.get("scopes") or [KnowledgeScope.SHARED.value])
        if s in (KnowledgeScope.LOCAL.value, KnowledgeScope.SHARED.value)
    ]
    top_k = _int(args.get("top_k"), 5)
    from ..agents.retrieval import RetrievalAgent

    out = RetrievalAgent(db_path=_db_path(args)).run(
        RetrievalInput(
            query=query,
            scopes=scopes or [KnowledgeScope.SHARED],
            method=RetrievalMethod.HYBRID,
            top_k=top_k,
        )
    )
    return ToolResult(
        name="diagnosis.retrieve_knowledge",
        ok=True,
        observation=f"retrieved {len(out.chunks)} knowledge chunks; method={out.method_used.value}",
        data={"chunks": [c.model_dump(mode="json") for c in out.chunks], "degraded": out.degraded},
    )


def analyze_code_static(args: Dict[str, Any]) -> ToolResult:
    code = str(args.get("code") or "")
    language = str(args.get("language") or "python").lower()
    findings: List[Dict[str, str]] = []
    if not code.strip():
        return ToolResult(
            name="diagnosis.analyze_code_static",
            ok=True,
            observation="no code supplied",
            data={"findings": findings},
        )
    if language == "python":
        findings.extend(_python_static_findings(code))
    else:
        findings.append({
            "kind": "unsupported_language",
            "message": f"static analysis is only implemented for python in phase 1: {language}",
        })
    return ToolResult(
        name="diagnosis.analyze_code_static",
        ok=True,
        observation=f"static analysis produced {len(findings)} findings",
        data={"findings": findings},
    )


def _python_static_findings(code: str) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [{"kind": "syntax_error", "message": str(exc)}]

    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            if isinstance(node.iter, ast.Call) and _name(node.iter.func) == "range":
                if len(node.iter.args) == 1 and _literal_int(node.iter.args[0]) == len(code):
                    findings.append({
                        "kind": "suspicious_range",
                        "message": "range(len(code)) usually means the literal source length, not data length.",
                    })
        if isinstance(node, ast.FunctionDef) and _looks_recursive(node):
            if not _has_obvious_base_case(node):
                findings.append({
                    "kind": "recursion_base_case",
                    "message": f"recursive function {node.name!r} has no obvious base-case return.",
                })
    return findings


def _looks_recursive(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _name(child.func) == node.name:
            return True
    return False


def _has_obvious_base_case(node: ast.FunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.If):
            for item in child.body:
                if isinstance(item, ast.Return):
                    return True
    return False


def _name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_int(node: ast.AST) -> Optional[int]:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _db_path(args: Dict[str, Any]) -> Optional[str]:
    value = args.get("db_path")
    return str(value) if value else None


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x) for x in value if x is not None]
    if isinstance(value, tuple):
        return [str(x) for x in value if x is not None]
    return [str(value)]


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
