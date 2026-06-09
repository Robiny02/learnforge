"""Repo Map —— 把 GitHub 仓库动态解析成结构化地图，再据此动态选要读的文件。

不猜文件、不写死文件名/项目专属规则：先 `build_repo_map` 把真实仓库树解析成带「角色」的 FileEntry
（doc/source/test/config/example/script/unknown），再 `select_files` 综合
①claim token 与 path/filename 的相关性 ②文档重要性 ③入口/核心信号 ④角色多样性 ⑤预算
选出可解释的文件清单（每个带 score / selected_reason / expected_claims）。

纯函数、可离线测试：`build_repo_map` 接收已取回的 repo_summary / list_tree payload，不自己联网。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ...contracts.agents.diagnosis import SelectedFile

# 角色判定用的**通用结构信号**（与具体项目无关）。
_DOC_EXTS = (".md", ".rst", ".markdown", ".txt", ".adoc")
_SRC_EXTS = (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".cpp", ".cc",
             ".c", ".h", ".rb", ".kt", ".scala", ".php", ".cs")
_CONFIG_EXTS = (".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".lock", ".env",
                ".properties", ".gradle")
_CONFIG_NAMES = ("dockerfile", "makefile", ".gitignore", ".dockerignore", "requirements.txt",
                 "setup.cfg", "pyproject.toml", "package.json", "go.mod", "cargo.toml", "pom.xml")
_TEST_SEGS = ("test", "tests", "spec", "specs", "__tests__", "e2e")
_EXAMPLE_SEGS = ("example", "examples", "demo", "demos", "sample", "samples")
_SCRIPT_SEGS = ("script", "scripts", "bin", "tools")
# 噪声目录（依赖/构建/产物）——不进 repo map 选择。
_NOISE_DIRS = ("node_modules", "vendor", "dist", "build", "target", ".git", ".venv",
               "site-packages", "third_party", "fixtures", "testdata", "__pycache__")
# 通用入口/核心文件名信号（非项目特定）。
_ENTRYPOINT_STEMS = ("main", "index", "__main__", "__init__", "app", "cli", "server", "run",
                     "core", "engine", "router", "graph")
_STOP = {"the", "and", "with", "for", "using", "use", "used", "based", "via", "into", "from",
         "that", "this", "system", "design", "designed", "build", "built", "implement",
         "implemented", "support", "develop", "project", "module", "core", "main", "data",
         "api", "app", "service", "model", "auto", "self", "test", "src", "lib"}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_SPLIT_RE = re.compile(r"[/_.\-]+")


def _split_tokens(text: str) -> Set[str]:
    """把任意词拆成小写 token（含 CamelCase / snake / 路径分隔），剔停用词。"""
    toks: Set[str] = set()
    for raw in _SPLIT_RE.split(text or ""):
        for m in _TOKEN_RE.finditer(raw):
            w = m.group(0)
            for part in [w] + re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+", w):
                pl = part.lower()
                if len(pl) >= 3 and pl not in _STOP:
                    toks.add(pl)
    return toks


def claim_tokens(text: str) -> Set[str]:
    """从简历/诊断目标动态抽技术 token（候选人自己的术语，无项目专属词表）。"""
    return _split_tokens(text)


def _is_noise(path: str) -> bool:
    segs = path.lower().split("/")
    return any(s in _NOISE_DIRS or s.startswith(".") and s in _NOISE_DIRS for s in segs) \
        or any(n in path.lower() for n in _NOISE_DIRS)


def infer_role(path: str) -> str:
    """结构化推断文件角色（通用，不依赖项目专属文件名）。"""
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    segs = low.split("/")
    ext = ("." + name.rsplit(".", 1)[-1]) if "." in name else ""
    # 顺序：test > config > example/script(目录) > doc > source > unknown
    if any(s in _TEST_SEGS for s in segs) or re.match(r"(test_|.*_test\.|.*\.test\.|.*\.spec\.)", name):
        return "test"
    if ext in _CONFIG_EXTS or name in _CONFIG_NAMES:
        return "config"
    if any(s in _EXAMPLE_SEGS for s in segs):
        return "example"
    if any(s in _SCRIPT_SEGS for s in segs) and ext in _SRC_EXTS:
        return "script"
    if ext in _DOC_EXTS:
        return "doc"
    if ext in _SRC_EXTS:
        return "source"
    return "unknown"


_ROLE_TO_EVIDENCE = {"doc": "doc", "source": "code", "test": "test", "config": "config",
                     "example": "code", "script": "code", "unknown": "doc"}


@dataclass
class FileEntry:
    path: str
    name: str
    ext: str
    depth: int
    size: int
    role: str
    path_tokens: Set[str] = field(default_factory=set)
    name_tokens: Set[str] = field(default_factory=set)


@dataclass
class RepoMap:
    repo: str
    description: str = ""
    languages: List[str] = field(default_factory=list)
    stars: Optional[int] = None
    files: List[FileEntry] = field(default_factory=list)

    def by_role(self, role: str) -> List[FileEntry]:
        return [f for f in self.files if f.role == role]

    @property
    def root_docs(self) -> List[FileEntry]:
        return [f for f in self.files if f.role == "doc" and f.depth == 0]

    def summary(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for f in self.files:
            out[f.role] = out.get(f.role, 0) + 1
        return out


def build_repo_map(repo: str, summary_payload: Dict, tree_payload: Dict) -> RepoMap:
    """把 repo_summary + list_tree(recursive) 解析成 RepoMap（纯函数，不联网）。"""
    rm = RepoMap(
        repo=repo,
        description=str(summary_payload.get("description") or ""),
        languages=list((summary_payload.get("languages") or {}).keys()),
        stars=summary_payload.get("stars"),
    )
    for t in (tree_payload.get("tree") or []):
        if t.get("type") != "blob":
            continue
        path = t.get("path", "")
        if not path or _is_noise(path):
            continue
        name = path.rsplit("/", 1)[-1]
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        rm.files.append(FileEntry(
            path=path, name=name, ext=ext, depth=path.count("/"),
            size=int(t.get("size") or 0), role=infer_role(path),
            path_tokens=_split_tokens(path), name_tokens=_split_tokens(name),
        ))
    return rm


# --------------------------------------------------------------------------- 动态选择
def _doc_importance(f: FileEntry) -> float:
    """文档重要性（结构信号）：root/docs 位置 + 浅 + 大。"""
    score = 2.0
    if f.depth == 0:
        score += 2.0
    if f.path.lower().split("/", 1)[0] == "docs" or "/docs/" in f.path.lower():
        score += 1.5
    score += min(1.5, (f.size or 0) / 4000.0)
    score -= 0.3 * f.depth
    return score


def _token_relevance(f: FileEntry, tokens: Set[str]) -> tuple:
    """(命中分, 命中的 token 列表)：文件名命中权重高于路径命中。"""
    name_hits = sorted(tokens & f.name_tokens)
    path_hits = sorted((tokens & f.path_tokens) - set(name_hits))
    score = 2.0 * len(name_hits) + 1.0 * len(path_hits)
    return score, (name_hits + path_hits)


def select_files(repo_map: RepoMap, tokens: Set[str], budget: int = 5,
                 deep: bool = True) -> List[SelectedFile]:
    """据 repo map + claim token 动态选文件（可解释）。

    综合：token 相关性 + 文档重要性 + 入口信号 + 角色多样性 + 预算。返回带 reason/score 的 SelectedFile。
    fast(deep=False) 只选文档；deep across docs/source/test/config 选并保多样性。
    """
    selections: List[SelectedFile] = []
    chosen: Set[str] = set()

    def _add(f: FileEntry, score: float, reason: str, expected: List[str]) -> None:
        if f.path in chosen or len(selections) >= budget:
            return
        chosen.add(f.path)
        selections.append(SelectedFile(
            path=f.path, role=f.role, evidence_kind=_ROLE_TO_EVIDENCE.get(f.role, "doc"),
            score=round(score, 2), selected_reason=reason, expected_claims=expected,
        ))

    # 1) 文档：按重要性挑前若干（README 由调用方经 repo_summary 单独加，这里排除以免重复读）。
    docs = sorted([f for f in repo_map.by_role("doc") if "readme" not in f.name.lower()],
                  key=lambda f: -_doc_importance(f))
    for f in docs[: (2 if deep else budget)]:
        rel, hits = _token_relevance(f, tokens)
        reason = ("命中 claim：" + "、".join(hits)) if hits else ("核心说明文档（root/docs，体量大）")
        _add(f, _doc_importance(f) + rel, reason, hits)

    if not deep:
        return selections[:budget]

    # 2) source / test / config：按 token 相关性排序，保证角色多样性。
    def _ranked(role: str) -> List[tuple]:
        out = []
        for f in repo_map.by_role(role):
            rel, hits = _token_relevance(f, tokens)
            entry = 1.0 if f.name.rsplit(".", 1)[0].lower() in _ENTRYPOINT_STEMS else 0.0
            if rel > 0 or (role == "source" and entry > 0):
                out.append((rel + entry, hits, entry, f))
        out.sort(key=lambda x: (-x[0], x[3].depth, x[3].path))
        return out

    for role, label in (("source", "源码"), ("test", "测试"), ("config", "配置")):
        for score, hits, entry, f in _ranked(role)[:2]:
            if len(selections) >= budget:
                break
            if hits:
                reason = f"{label}：命中 claim {('、'.join(hits))}"
            elif entry:
                reason = f"{label}：入口/核心文件信号（{f.name}）"
            else:
                reason = f"{label}相关文件"
            _add(f, score, reason, hits)

    # 3) 角色多样性兜底：预算有余且 test/config 未覆盖 → 补一个（最浅的），尽量覆盖 docs/source/tests/config。
    covered = {s.role for s in selections}
    for role, label in (("test", "测试"), ("config", "配置")):
        if len(selections) >= budget or role in covered:
            continue
        cands = sorted(repo_map.by_role(role), key=lambda f: (f.depth, f.path))
        if cands:
            _add(cands[0], 0.5, f"覆盖{label}类型（多样性）", [])

    # 4) 还有预算 → 用最相关的剩余文件填满（不限角色）。
    if len(selections) < budget:
        rest = []
        for f in repo_map.files:
            if f.path in chosen or f.role == "unknown":
                continue
            rel, hits = _token_relevance(f, tokens)
            if rel > 0:
                rest.append((rel, hits, f))
        rest.sort(key=lambda x: (-x[0], x[2].depth))
        for rel, hits, f in rest:
            _add(f, rel, f"{f.role}：命中 claim {('、'.join(hits))}", hits)
            if len(selections) >= budget:
                break
    return selections[:budget]
