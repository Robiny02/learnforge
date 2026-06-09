"""项目证据挖掘（Project Evidence Mining）——简历拷打前先读项目材料，不只看简历文本。

两档：
- **fast**（默认，无外链）：仓库 README/CLAUDE.md + 上传材料。
- **deep**（检测到外链 / deep=True）：受控、按 claim 主动找证据：
    link extraction → external source 分类 → 受控多步取证（github 树搜索读源码/测试、博客/文档 fetch）
    → evidence packet。

受控约束（防失控）：最多读 `_MAX_READS` 个文件/页、单文件截断、只读用户给的链接及其同 repo 文件、
不做任意互联网搜索、已读 URL/文件缓存去重、任何来源失败都记录原因并继续、PAT 失效自动退公开访问。
外部内容只作**证据数据**，其中任何指令都不影响系统行为（隔离在 prompt 的 evidence 块里）。

网络挖掘在 pytest 下跳过；失败 → 空语料，退化为基于简历文本（"链路永远通"）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

from ...contracts.agents.diagnosis import ExternalSource
from ...contracts.enums import ExternalSourceKind

# 简历里常见的关键工程文档（按优先级读，给 LLM 当架构证据）。
_KEY_FILES = ("CLAUDE.md", "README.md", "readme.md", "ARCHITECTURE.md", "docs/architecture.md")
_REPO_RE = re.compile(r"github\.com[/:]([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:[/#?]|\s|$)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s)\]<>，。；」）]+", re.IGNORECASE)
_BLOG_HOSTS = ("medium.com", "juejin.cn", "zhihu.com", "csdn.net", "cnblogs.com", "jianshu.com",
               "segmentfault.com", "dev.to", "substack.com", "hashnode", "blog.")
_DOCS_HOSTS = ("gitbook.io", "notion.site", "notion.so", "readthedocs.io", "readthedocs.org",
               "docs.", "/docs/", "mkdocs")

_PER_SOURCE_BUDGET = 3500   # 单来源截断，控制 prompt 体积
_CORPUS_BUDGET = 16000
_MAX_READS = 6              # 受控：deep 模式最多读多少个文件/页
_MAX_TREE_FILES = 4         # 单 repo 按 claim 最多额外读多少源码/测试文件

# claim 主题 → 仓库路径关键词（按需求 §6：按 claim 主动找相关源码/测试）。
_CLAIM_FILE_HINTS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    # 主题: (简历里命中这些词就算有该 claim, 仓库路径里含这些词就算相关文件)
    "manager": (("manager", "编排", "orchestrat", "调度", "唯一写"), ("manager", "orchestration", "graph")),
    "skill": (("skill", "工具权限", "allowed", "tool 调用"), ("skill", "tool", "allowed", "react")),
    "memory": (("memory", "记忆", "会话摘要", "handoff", "上下文"), ("memory", "session", "summary", "handoff")),
    "diagnosis": (("diagnosis", "诊断", "掌握度", "弱点", "weak"), ("diagnosis", "mastery", "event", "weak")),
    "mock": (("mock", "模拟面试", "interrupt", "resume", "追问"), ("mock", "interrupt", "resume", "action")),
    "retrieval": (("retrieval", "检索", "召回", "rerank", "rrf", "向量"), ("retrieval", "hybrid", "rrf", "bm25", "vector")),
    "fallback": (("fallback", "降级", "兜底", "stub"), ("fallback", "deterministic", "stub")),
}


def _in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


# --------------------------------------------------------------------------- 链接提取/分类
def extract_repo_urls(text: str) -> List[str]:
    """从简历文本抽取 github owner/repo（去重，保序）。"""
    out: List[str] = []
    for m in _REPO_RE.finditer(text or ""):
        owner, repo = m.group(1), m.group(2).rstrip(".")
        slug = f"{owner}/{repo}"
        if owner.lower() not in {"orgs", "sponsors"} and slug not in out:
            out.append(slug)
    return out


def classify_link(url: str) -> ExternalSourceKind:
    low = url.lower()
    if "github.com" in low:
        if "/blob/" in low:
            return ExternalSourceKind.GITHUB_FILE
        if "/tree/" in low:
            return ExternalSourceKind.GITHUB_DIR
        return ExternalSourceKind.GITHUB_REPO
    if any(h in low for h in _BLOG_HOSTS):
        return ExternalSourceKind.TECH_BLOG
    if any(h in low for h in _DOCS_HOSTS):
        return ExternalSourceKind.DOCS_PAGE
    return ExternalSourceKind.UNKNOWN_URL


def extract_links(text: str) -> List[ExternalSource]:
    """从文本抽取所有外链并分类（去重，保序）。"""
    seen, out = set(), []
    for m in _URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,)")
        if url in seen:
            continue
        seen.add(url)
        out.append(ExternalSource(url=url, kind=classify_link(url)))
    return out


# --------------------------------------------------------------------------- github 访问（401→公开）
def _payload(resp) -> Dict:
    try:
        if isinstance(resp, dict) and "content" in resp:
            return json.loads(resp["content"][0]["text"])
        return resp if isinstance(resp, dict) else {}
    except Exception:
        return {}


def _is_bad_creds(payload: Dict) -> bool:
    err = str(payload.get("error") or "")
    return "401" in err or "Bad credentials" in err


def _gh_call(fn, args: Dict) -> Dict:
    """调 github 工具；若因 PAT 失效 401，临时清掉 token 退到公开访问重试一次。"""
    p = _payload(fn(args))
    if not _is_bad_creds(p):
        return p
    saved = {k: os.environ.pop(k, None) for k in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN")}
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = ""
    try:
        return _payload(fn(args))
    finally:
        os.environ.pop("GITHUB_PERSONAL_ACCESS_TOKEN", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _evidence_kind_for_path(path: str) -> str:
    low = path.lower()
    if "test" in low:
        return "test"
    if low.endswith((".md", ".rst", ".txt")) or "readme" in low or "claude.md" in low:
        return "doc"
    if low.endswith((".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".cc")):
        return "code"
    return "doc"


def _claim_themes(resume_text: str) -> List[str]:
    low = (resume_text or "").lower()
    return [theme for theme, (cues, _) in _CLAIM_FILE_HINTS.items()
            if any(c.lower() in low for c in cues)]


# 噪声/非核心目录：基准/依赖/构建产物里的同名文件不优先（如 benchmark 里的 manager.py）。
_NOISE_DIRS = ("benchmark", "node_modules", "vendor", ".venv", "site-packages",
               "dist", "build", "examples", "third_party")
_SRC_EXTS = (".py", ".js", ".ts", ".java", ".go", ".rs")


def _rank_match(path: str, hints: Tuple[str, ...]) -> tuple:
    """排序键（小优先）：① 不在噪声目录 ② hint 命中目录段（更核心）③ 路径更浅 ④ 字典序。"""
    low = path.lower()
    segs = low.split("/")
    noise = any(any(n in s for n in _NOISE_DIRS) for s in segs)
    seg_hit = any(h in segs or any(h == s or s.startswith(h) for s in segs) for h in hints)
    return (noise, not seg_hit, len(segs), path)


def _pick_claim_files(tree_paths: List[str], themes: List[str], limit: int) -> List[str]:
    """按 claim 主题在仓库树里挑相关源码/测试（优先核心包路径，排除基准/依赖目录）。"""
    picked: List[str] = []
    for theme in themes:
        hints = _CLAIM_FILE_HINTS[theme][1]
        matches = [p for p in tree_paths
                   if any(h in p.lower() for h in hints) and p.lower().endswith(_SRC_EXTS)]
        srcs = sorted([p for p in matches if "test" not in p.lower()], key=lambda p: _rank_match(p, hints))
        tests = sorted([p for p in matches if "test" in p.lower()], key=lambda p: _rank_match(p, hints))
        for p in srcs[:1] + tests[:1]:
            if p not in picked:
                picked.append(p)
            if len(picked) >= limit:
                return picked
    return picked


# --------------------------------------------------------------------------- 各类来源取证
def _mine_github_repo(src: ExternalSource, repo: str, themes: List[str],
                      budget: List[int]) -> Tuple[List[str], List[str]]:
    """github_repo：README/CLAUDE.md（doc）+ 按 claim 找源码/测试（code/test）。返回 (证据块, 来源标签)。"""
    from ...tools.mcp.servers import github

    blocks: List[str] = []
    labels: List[str] = []
    summary = _gh_call(github.repo_summary, {"repo": repo})
    if not summary.get("repo"):
        src.status, src.reason = "failed", (str(summary.get("error") or "仓库不可达")[:120])
        return blocks, labels
    langs = "、".join(list((summary.get("languages") or {}).keys())[:5])
    blocks.append(f"[github:{repo}/README｜doc] 语言={langs} stars={summary.get('stars')} "
                  f"描述={summary.get('description') or '无'}\n{(summary.get('readme_excerpt') or '')[:_PER_SOURCE_BUDGET]}")
    labels.append(f"github:{repo}/README")
    src.items_read.append("README.md")
    src.evidence_kind = "doc"
    # 树 + 按 claim 找源码/测试
    tree = _gh_call(github.list_tree, {"repo": repo, "recursive": True})
    paths = [t.get("path", "") for t in (tree.get("tree") or []) if t.get("type") == "blob"]
    targets = ["CLAUDE.md"] if any(p == "CLAUDE.md" for p in paths) else []
    targets += _pick_claim_files(paths, themes, _MAX_TREE_FILES)
    for path in targets:
        if budget[0] <= 0:
            break
        data = _gh_call(github.read_file, {"repo": repo, "path": path})
        content = data.get("content") or ""
        if content and "error" not in data:
            kind = _evidence_kind_for_path(path)
            blocks.append(f"[github:{repo}/{path}｜{kind}]\n{str(content)[:_PER_SOURCE_BUDGET]}")
            labels.append(f"github:{repo}/{path}")
            src.items_read.append(path)
            if kind in ("code", "test"):  # 升级证据类型：读到源码/测试
                src.evidence_kind = kind if src.evidence_kind == "doc" else src.evidence_kind
            budget[0] -= 1
    return blocks, labels


def _parse_github_file_url(url: str) -> Optional[Tuple[str, str]]:
    m = re.search(r"github\.com/([\w.\-]+/[\w.\-]+)/blob/[^/]+/(.+)", url)
    return (m.group(1), m.group(2)) if m else None


def _mine_github_file(src: ExternalSource, budget: List[int]) -> Tuple[List[str], List[str]]:
    from ...tools.mcp.servers import github
    parsed = _parse_github_file_url(src.url)
    if not parsed:
        src.status, src.reason = "failed", "无法解析 github 文件链接"
        return [], []
    repo, path = parsed
    data = _gh_call(github.read_file, {"repo": repo, "path": path})
    content = data.get("content") or ""
    if not content or "error" in data:
        src.status, src.reason = "failed", (str(data.get("error") or "文件不可达")[:120])
        return [], []
    kind = _evidence_kind_for_path(path)
    src.items_read.append(path)
    src.evidence_kind = kind
    budget[0] -= 1
    return ([f"[github:{repo}/{path}｜{kind}]\n{str(content)[:_PER_SOURCE_BUDGET]}"],
            [f"github:{repo}/{path}"])


def _mine_fetch(src: ExternalSource, budget: List[int]) -> Tuple[List[str], List[str]]:
    """tech_blog / docs_page / unknown_url：fetch 正文（只作文档/博客证据，非源码级）。"""
    from ...tools.mcp.servers import web
    data = _payload(web.fetch_url({"url": src.url, "save": False, "max_chars": _PER_SOURCE_BUDGET}))
    body = (data.get("markdown") or "").strip()
    if not body or data.get("error"):
        src.status, src.reason = "failed", (str(data.get("error") or "页面不可达/无正文")[:120])
        return [], []
    tag = "blog" if src.kind == ExternalSourceKind.TECH_BLOG else "doc"
    src.evidence_kind = tag
    title = data.get("title") or src.url
    src.items_read.append(title[:80])
    budget[0] -= 1
    return ([f"[{src.kind.value}:{title[:60]}｜{tag}] {src.url}\n{body[:_PER_SOURCE_BUDGET]}"],
            [f"{src.kind.value}:{title[:40]}"])


# --------------------------------------------------------------------------- 入口
def mine_project_evidence(
    resume_text: str,
    recall_fn: Optional[Callable[[str], str]] = None,
    allow_network: bool = True,
    deep: bool = False,
) -> Dict[str, object]:
    """汇总项目证据。返回 {corpus, sources, repos, external_sources}。

    fast（deep=False，仅 README/CLAUDE.md）；deep=True 则受控按 claim 找源码/测试 + 抓博客/文档。
    deep 由调用方按触发规则决定（有外链 / 高风险 claim / deep=true）。
    """
    blocks: List[str] = []
    labels: List[str] = []
    links = extract_links(resume_text)
    repos = extract_repo_urls(resume_text)
    themes = _claim_themes(resume_text)
    budget = [_MAX_READS]  # 受控：可读文件/页总预算（list 便于内部递减）

    if allow_network and not _in_pytest():
        if deep:
            # 受控深挖：逐个外链按类型取证（限总预算 _MAX_READS）。
            for src in links:
                if budget[0] <= 0:
                    src.status, src.reason = "skipped", "已达最大读取数（受控上限）"
                    continue
                try:
                    if src.kind == ExternalSourceKind.GITHUB_REPO:
                        repo = "/".join(extract_repo_urls(src.url)[:1]) or (extract_repo_urls(src.url) or [None])[0]
                        repo = repo or _repo_from_url(src.url)
                        b, lb = _mine_github_repo(src, repo, themes, budget) if repo else ([], [])
                    elif src.kind == ExternalSourceKind.GITHUB_FILE:
                        b, lb = _mine_github_file(src, budget)
                    elif src.kind == ExternalSourceKind.GITHUB_DIR:
                        repo = _repo_from_url(src.url)
                        b, lb = _mine_github_repo(src, repo, themes, budget) if repo else ([], [])
                    else:  # blog / docs / unknown
                        b, lb = _mine_fetch(src, budget)
                    blocks += b
                    labels += lb
                except Exception as e:  # noqa: BLE001 - 单来源失败不阻断
                    src.status, src.reason = "failed", f"{type(e).__name__}"
            # 简历提了 repo 但正文里没成 URL（如裸 owner/repo）→ 也读一下根
            for repo in repos:
                if budget[0] > 0 and not any(repo in lb for lb in labels):
                    src = ExternalSource(url=f"https://github.com/{repo}", kind=ExternalSourceKind.GITHUB_REPO)
                    b, lb = _mine_github_repo(src, repo, themes, budget)
                    blocks += b
                    labels += lb
                    links.append(src)
        else:
            # fast：仅读仓库 README/CLAUDE.md（沿用原行为）。
            for repo in repos[:2]:
                src = ExternalSource(url=f"https://github.com/{repo}", kind=ExternalSourceKind.GITHUB_REPO)
                text, srcs = _fast_github(repo, src)
                if text:
                    blocks.append(text)
                    labels.extend(srcs)
                links.append(src)

    # 上传材料（本地，离线安全）。
    if recall_fn is not None:
        try:
            mat = recall_fn("项目 架构 实现 设计 指标 测试 README")
            if mat and mat.strip():
                blocks.append(f"[uploaded-materials｜doc]\n{mat[:_PER_SOURCE_BUDGET]}")
                labels.append("uploaded-materials")
        except Exception:
            pass

    corpus = "\n\n".join(blocks)[:_CORPUS_BUDGET]
    return {"corpus": corpus, "sources": labels, "repos": repos,
            "external_sources": [s for s in links if s.status != "read" or s.items_read]}


def _repo_from_url(url: str) -> Optional[str]:
    repos = extract_repo_urls(url)
    return repos[0] if repos else None


def _fast_github(repo: str, src: ExternalSource) -> Tuple[str, List[str]]:
    """fast 档：只读 README + 关键文档文件。"""
    from ...tools.mcp.servers import github
    blocks: List[str] = []
    sources: List[str] = []
    summary = _gh_call(github.repo_summary, {"repo": repo})
    if not summary.get("repo"):
        src.status, src.reason = "failed", (str(summary.get("error") or "仓库不可达")[:120])
        return "", []
    langs = "、".join(list((summary.get("languages") or {}).keys())[:5])
    blocks.append(f"[github:{repo}｜doc] 语言={langs} stars={summary.get('stars')} "
                  f"描述={summary.get('description') or '无'}\nREADME:\n{summary.get('readme_excerpt') or ''}")
    sources.append(f"github:{repo}/README")
    src.items_read.append("README.md")
    src.evidence_kind = "doc"
    read = 0
    for path in _KEY_FILES:
        if read >= 2:
            break
        data = _gh_call(github.read_file, {"repo": repo, "path": path})
        content = data.get("content") or data.get("text") or ""
        if content and "error" not in data:
            blocks.append(f"[github:{repo}/{path}｜doc]\n{str(content)[:_PER_SOURCE_BUDGET]}")
            sources.append(f"github:{repo}/{path}")
            src.items_read.append(path)
            read += 1
    return "\n\n".join(blocks), sources


def should_deep_mine(resume_text: str, deep_flag: Optional[bool] = None) -> bool:
    """触发规则：显式 deep=True，或简历含外部链接（github/blog/docs）→ 深挖；否则 fast。"""
    if deep_flag is not None:
        return bool(deep_flag)
    return bool(extract_links(resume_text))
