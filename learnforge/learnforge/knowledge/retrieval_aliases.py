"""Config-driven retrieval query alias expansion.

Alias rules live in JSON so retrieval experiments can swap them without touching code.
Set `LF_RETRIEVAL_ALIAS_PATH=/path/to/aliases.json` to evaluate a different rule set.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


DEFAULT_ALIAS_PATH = Path(__file__).with_name("retrieval_aliases.json")


@dataclass(frozen=True)
class RetrievalAliasRule:
    rule_id: str
    modes: List[str]
    rewrite: str
    any_terms: List[str]
    all_terms: List[str]

    def matches(self, query: str, mode: str) -> bool:
        if mode not in self.modes:
            return False
        if self.all_terms and all(term in query for term in self.all_terms):
            return True
        if self.any_terms and any(term in query for term in self.any_terms):
            return True
        return False


def load_alias_rules(path: Optional[str] = None) -> List[RetrievalAliasRule]:
    """Load alias rules from JSON. Invalid entries are skipped."""
    source = Path(path or os.getenv("LF_RETRIEVAL_ALIAS_PATH") or DEFAULT_ALIAS_PATH)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except Exception:
        return []
    rules = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(rules, list):
        return []
    out: List[RetrievalAliasRule] = []
    for i, item in enumerate(rules):
        if not isinstance(item, dict):
            continue
        rewrite = str(item.get("rewrite") or "").strip()
        modes = [str(m) for m in item.get("modes") or [] if str(m)]
        any_terms = [str(t) for t in item.get("any") or [] if str(t)]
        all_terms = [str(t) for t in item.get("all") or [] if str(t)]
        if not rewrite or not modes or not (any_terms or all_terms):
            continue
        out.append(
            RetrievalAliasRule(
                rule_id=str(item.get("id") or f"rule_{i}"),
                modes=modes,
                rewrite=rewrite,
                any_terms=any_terms,
                all_terms=all_terms,
            )
        )
    return out


def expand_aliases(query: str, mode: str, path: Optional[str] = None) -> List[str]:
    """Return configured rewrites matching query/mode, preserving file order."""
    q = query or ""
    rewrites: List[str] = []
    for rule in load_alias_rules(path):
        if rule.matches(q, mode):
            rewrites.append(rule.rewrite)
    return _dedupe(rewrites)


def _dedupe(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        s = str(item).strip()
        if s and s not in out:
            out.append(s)
    return out
