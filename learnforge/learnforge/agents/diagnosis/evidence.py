"""项目证据挖掘（Project Evidence Mining）——简历拷打前先读项目材料，不只看简历文本。

来源（best-effort，按可得性叠加）：
- **GitHub 仓库**：简历里出现的 github.com/owner/repo → repo_summary(README/语言/元数据) + 关键文件
  （CLAUDE.md / README.md / 架构文档）。PAT 失效(401)时自动退到**公开访问**（公开仓库无需 token）。
- **上传材料**：用户上传的简历/项目说明（local chunks, origin=attachment），经传入的 recall 回调召回。

产出一份带来源标注的「证据语料」字符串，喂给项目级诊断的 LLM。任何来源失败都不阻断
（无网络/无仓库/无附件 → 空语料，诊断退化为基于简历文本，"链路永远通"）。网络挖掘在 pytest 下跳过。
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

# 简历里常见的关键工程文档（按优先级读，给 LLM 当架构证据）。
_KEY_FILES = ("CLAUDE.md", "README.md", "readme.md", "ARCHITECTURE.md", "docs/architecture.md")
_REPO_RE = re.compile(r"github\.com[/:]([\w.\-]+)/([\w.\-]+?)(?:\.git)?(?:[/#?]|\s|$)", re.IGNORECASE)
_PER_SOURCE_BUDGET = 4000   # 单来源截断，控制 prompt 体积
_CORPUS_BUDGET = 12000


def _in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def extract_repo_urls(text: str) -> List[str]:
    """从简历文本抽取 github owner/repo（去重，保序）。"""
    out: List[str] = []
    for m in _REPO_RE.finditer(text or ""):
        owner, repo = m.group(1), m.group(2).rstrip(".")
        slug = f"{owner}/{repo}"
        if owner.lower() not in {"orgs", "sponsors"} and slug not in out:
            out.append(slug)
    return out


def _payload(resp) -> Dict:
    """解析 MCP 风格 {content:[{text}]} 或直接 dict。失败 → {}。"""
    try:
        if isinstance(resp, dict) and "content" in resp:
            return json.loads(resp["content"][0]["text"])
        return resp if isinstance(resp, dict) else {}
    except Exception:
        return {}


def _is_bad_creds(payload: Dict) -> bool:
    return "401" in str(payload.get("error") or "") or "Bad credentials" in str(payload.get("error") or "")


def _gh_call(fn, args: Dict) -> Dict:
    """调 github 工具；若因 PAT 失效 401，则临时清掉 token 退到公开访问重试一次。"""
    p = _payload(fn(args))
    if not _is_bad_creds(p):
        return p
    saved = {k: os.environ.pop(k, None) for k in ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN")}
    os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = ""  # 强制无 Authorization 头 → 公开访问
    try:
        return _payload(fn(args))
    finally:
        os.environ.pop("GITHUB_PERSONAL_ACCESS_TOKEN", None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def mine_github(repo: str, max_files: int = 2) -> Tuple[str, List[str]]:
    """读一个仓库：summary(README+语言) + 关键文件。返回 (证据文本, 来源列表)。"""
    try:
        from ...tools.mcp.servers import github
    except Exception:
        return "", []
    blocks: List[str] = []
    sources: List[str] = []
    summary = _gh_call(github.repo_summary, {"repo": repo})
    if summary.get("repo"):
        langs = "、".join(list((summary.get("languages") or {}).keys())[:5])
        head = (f"[github:{repo}] 语言={langs} stars={summary.get('stars')} "
                f"描述={summary.get('description') or '无'}\nREADME:\n{summary.get('readme_excerpt') or ''}")
        blocks.append(head[:_PER_SOURCE_BUDGET])
        sources.append(f"github:{repo}/README")
        read = 0
        for path in _KEY_FILES:
            if read >= max_files:
                break
            data = _gh_call(github.read_file, {"repo": repo, "path": path})
            content = data.get("content") or data.get("text") or ""
            if content and "error" not in data:
                blocks.append(f"[github:{repo}/{path}]\n{str(content)[:_PER_SOURCE_BUDGET]}")
                sources.append(f"github:{repo}/{path}")
                read += 1
    return "\n\n".join(blocks), sources


def mine_project_evidence(
    resume_text: str,
    recall_fn: Optional[Callable[[str], str]] = None,
    allow_network: bool = True,
) -> Dict[str, object]:
    """汇总项目证据。返回 {corpus: str, sources: [str], repos: [str]}。

    recall_fn(query)->str：调用方（DiagnosisAgent）传入的本地材料检索（origin=attachment），离线安全。
    allow_network：github 挖掘开关（默认开；pytest 下强制关）。
    """
    blocks: List[str] = []
    sources: List[str] = []
    repos = extract_repo_urls(resume_text)

    # 1) GitHub（项目特异证据最强；PAT 失效自动退公开访问）。
    if allow_network and not _in_pytest():
        for repo in repos[:2]:
            text, srcs = mine_github(repo)
            if text:
                blocks.append(text)
                sources.extend(srcs)

    # 2) 上传的项目材料/简历说明（本地，离线安全）。
    if recall_fn is not None:
        try:
            mat = recall_fn("项目 架构 实现 设计 指标 测试 README")
            if mat and mat.strip():
                blocks.append(f"[uploaded-materials]\n{mat[:_PER_SOURCE_BUDGET]}")
                sources.append("uploaded-materials")
        except Exception:
            pass

    corpus = "\n\n".join(blocks)[:_CORPUS_BUDGET]
    return {"corpus": corpus, "sources": sources, "repos": repos}
