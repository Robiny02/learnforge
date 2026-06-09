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
_MAX_DOCS = 2               # 除 README 外，自主挑多少篇核心说明文档

# 文档/源码扩展名（结构信号，非项目特定）。
_DOC_EXTS = (".md", ".rst", ".markdown", ".txt", ".adoc")
_SRC_EXTS = (".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs", ".cpp", ".cc", ".c", ".rb", ".kt")
# 噪声/非核心目录：基准/依赖/构建产物不优先（通用，与具体项目无关）。
_NOISE_DIRS = ("benchmark", "node_modules", "vendor", ".venv", "site-packages",
               "dist", "build", "examples", "third_party", "fixtures", "testdata")
# 通用英文停用词（抽 claim 技术 token 时剔除，非项目特定）。
_STOP = {"the", "and", "with", "for", "using", "use", "used", "based", "via", "into", "from",
         "that", "this", "system", "design", "designed", "build", "built", "implement",
         "implemented", "support", "supports", "develop", "developed", "project", "module",
         "core", "main", "data", "api", "app", "service", "model", "based", "auto", "self"}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


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
    if "test" in low or "spec" in low:
        return "test"
    if low.endswith(_DOC_EXTS) or "readme" in low:
        return "doc"
    if low.endswith(_SRC_EXTS):
        return "code"
    return "doc"


def _is_noise(path: str) -> bool:
    segs = path.lower().split("/")
    return any(any(n in s for n in _NOISE_DIRS) for s in segs)


def _rank_important_docs(blobs: List[Tuple[str, int]]) -> List[str]:
    """从真实仓库树里**自主**挑最重要的说明文档（结构信号，不依赖任何写死的文件名/主题）。

    排序键（小优先）：① 根目录或 docs/ 下（说明文档惯例位置）② 路径更浅 ③ 体量更大（更实质）。
    README 由 repo_summary 单独取，这里挑 README 之外的核心文档。
    """
    cands = []
    for path, size in blobs:
        low = path.lower()
        if not low.endswith(_DOC_EXTS) or _is_noise(low) or "readme" in low.rsplit("/", 1)[-1]:
            continue
        depth = low.count("/")
        in_doc_loc = 0 if (depth == 0 or low.split("/", 1)[0] == "docs" or "/docs/" in low) else 1
        cands.append((in_doc_loc, depth, -int(size or 0), path))
    cands.sort()
    return [p for *_, p in cands]


def claim_tokens(text: str) -> set:
    """从简历正文**动态**抽取技术 token（英文词/标识符，含 CamelCase/snake 拆分），用于匹配仓库路径。

    不依赖任何项目特定词表——用候选人自己写的术语（Manager/ReAct/RocketMQ/JWT...）去仓库里找文件。
    """
    toks: set = set()
    for m in _TOKEN_RE.finditer(text or ""):
        w = m.group(0)
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|[0-9]+", w) or [w]
        for part in [w] + parts:
            pl = part.lower()
            if len(pl) >= 3 and pl not in _STOP:
                toks.add(pl)
    return toks


def _pick_claim_files(blobs: List[Tuple[str, int]], tokens: set, limit: int) -> List[str]:
    """用简历里抽出的技术 token，在真实仓库树里挑命中最多的源码/测试（无写死映射）。"""
    scored = []
    for path, _size in blobs:
        low = path.lower()
        if not low.endswith(_SRC_EXTS) or _is_noise(low):
            continue
        segs = re.split(r"[/_.\-]", low)
        hits = sum(1 for t in tokens if t in segs or any(t in s for s in segs))
        if hits == 0:
            continue
        is_test = 1 if ("test" in low or "spec" in low) else 0
        scored.append((-hits, is_test, low.count("/"), path))
    scored.sort()
    return [p for *_, p in scored][:limit]


# --------------------------------------------------------------------------- 各类来源取证
def _mine_github_repo(src: ExternalSource, repo: str, tokens: set,
                      budget: List[int], deep: bool = True) -> Tuple[List[str], List[str]]:
    """github_repo：README + **自主挑的核心说明文档**（doc）+（deep 时）按 claim token 找源码/测试。

    无任何写死的文件名/主题：文档从真实仓库树按结构信号排序，源码按简历技术 token 匹配。
    """
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
    # 拉真实仓库树，自主挑「最重要的说明文档」；deep 时再按 claim token 找源码/测试。
    tree = _gh_call(github.list_tree, {"repo": repo, "recursive": True})
    blobs = [(t.get("path", ""), t.get("size", 0))
             for t in (tree.get("tree") or []) if t.get("type") == "blob"]
    targets = _rank_important_docs(blobs)[:_MAX_DOCS]
    if deep:
        targets += _pick_claim_files(blobs, tokens, _MAX_TREE_FILES)
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
    tokens = claim_tokens(resume_text)  # 简历里的技术 token（动态，无写死映射）
    budget = [_MAX_READS]  # 受控：可读文件/页总预算（list 便于内部递减）

    if allow_network and not _in_pytest():
        if deep:
            # 受控深挖：逐个外链按类型取证（限总预算 _MAX_READS）。
            for src in links:
                if budget[0] <= 0:
                    src.status, src.reason = "skipped", "已达最大读取数（受控上限）"
                    continue
                try:
                    if src.kind in (ExternalSourceKind.GITHUB_REPO, ExternalSourceKind.GITHUB_DIR):
                        repo = _repo_from_url(src.url)
                        b, lb = _mine_github_repo(src, repo, tokens, budget) if repo else ([], [])
                    elif src.kind == ExternalSourceKind.GITHUB_FILE:
                        b, lb = _mine_github_file(src, budget)
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
                    b, lb = _mine_github_repo(src, repo, tokens, budget)
                    blocks += b
                    labels += lb
                    links.append(src)
        else:
            # fast：读 README + 自主挑的核心说明文档（不读源码）。
            for repo in repos[:2]:
                src = ExternalSource(url=f"https://github.com/{repo}", kind=ExternalSourceKind.GITHUB_REPO)
                b, lb = _mine_github_repo(src, repo, tokens, budget, deep=False)
                blocks += b
                labels += lb
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


def should_deep_mine(resume_text: str, deep_flag: Optional[bool] = None) -> bool:
    """触发规则：显式 deep=True，或简历含外部链接（github/blog/docs）→ 深挖；否则 fast。"""
    if deep_flag is not None:
        return bool(deep_flag)
    return bool(extract_links(resume_text))
