"""GitHub Markdown crawler for PostgreSQL knowledge ingestion.

The crawler intentionally does no text cleaning. It can either save raw
Markdown files for inspection or write JSONL documents that can be passed
directly to:

    python -m learnforge.knowledge.postgres.ingest --jsonl path/to/docs.jsonl

Example:

    python -m learnforge.knowledge.github_markdown_crawler \
        --repo https://github.com/liuup/claude-code-analysis \
        --out data/sources/liuup_claude-code-analysis.md.jsonl

    python -m learnforge.knowledge.github_markdown_crawler \
        --repo https://github.com/liuup/claude-code-analysis \
        --limit 1 \
        --raw-out-dir data/sources/liuup_claude-code-analysis/raw
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple


GITHUB_API = "https://api.github.com"
RAW_GITHUB = "https://raw.githubusercontent.com"
DEFAULT_TIMEOUT = 30
DEFAULT_USER_AGENT = "LearnForge GitHub Markdown Crawler"
_REPO_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+)")


class GitHubCrawlerError(RuntimeError):
    """Raised when GitHub crawling fails."""


def parse_github_repo(repo: str) -> Tuple[str, str]:
    """Return (owner, name) from a GitHub URL or owner/name string."""
    value = repo.strip().rstrip("/")
    match = _REPO_RE.match(value)
    if match:
        return match.group(1), match.group(2).removesuffix(".git")

    parts = value.split("/")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1].removesuffix(".git")

    raise ValueError("repo must be a GitHub URL or owner/name, e.g. liuup/claude-code-analysis")


def is_markdown_path(path: str) -> bool:
    return path.lower().endswith(".md")


def default_output_path(owner: str, repo: str) -> Path:
    return Path("data") / "sources" / f"{owner}_{repo}.md.jsonl"


class GitHubMarkdownCrawler:
    """Fetch all Markdown files from a GitHub repository into JSONL documents."""

    def __init__(
        self,
        token: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        pause_seconds: float = 0.0,
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._user_agent = user_agent
        self._pause_seconds = pause_seconds

    def crawl(
        self,
        repo: str,
        branch: Optional[str] = None,
        topic: str = "",
        source_type: str = "doc",
        limit: Optional[int] = None,
    ) -> Iterator[Dict]:
        owner, name = parse_github_repo(repo)
        ref = branch or self._default_branch(owner, name)
        tree = self._tree(owner, name, ref)

        markdown_items = [
            item for item in tree
            if item.get("type") == "blob" and is_markdown_path(str(item.get("path", "")))
        ]
        if limit is not None:
            markdown_items = markdown_items[:limit]
        for item in markdown_items:
            path = str(item["path"])
            text = self._raw(owner, name, ref, path)
            yield self._document(
                owner=owner,
                repo=name,
                branch=ref,
                path=path,
                text=text,
                item=item,
                topic=topic,
                source_type=source_type,
            )
            if self._pause_seconds > 0:
                time.sleep(self._pause_seconds)

    def write_jsonl(
        self,
        repo: str,
        out: Path,
        branch: Optional[str] = None,
        topic: str = "",
        source_type: str = "doc",
        limit: Optional[int] = None,
    ) -> int:
        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out.open("w", encoding="utf-8") as f:
            for doc in self.crawl(
                repo,
                branch=branch,
                topic=topic,
                source_type=source_type,
                limit=limit,
            ):
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
                count += 1
        return count

    def write_raw_markdown(
        self,
        repo: str,
        out_dir: Path,
        branch: Optional[str] = None,
        topic: str = "",
        source_type: str = "doc",
        limit: Optional[int] = None,
    ) -> List[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        for doc in self.crawl(
            repo,
            branch=branch,
            topic=topic,
            source_type=source_type,
            limit=limit,
        ):
            rel = Path(str(doc["metadata"]["github_path"]))
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(doc["text"]), encoding="utf-8")
            written.append(target)
        return written

    def _default_branch(self, owner: str, repo: str) -> str:
        meta = self._json(f"{GITHUB_API}/repos/{owner}/{repo}")
        branch = meta.get("default_branch")
        if not branch:
            raise GitHubCrawlerError(f"GitHub repo metadata missing default_branch: {owner}/{repo}")
        return str(branch)

    def _tree(self, owner: str, repo: str, branch: str) -> List[Dict]:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(branch)}?recursive=1"
        payload = self._json(url)
        if payload.get("truncated"):
            raise GitHubCrawlerError(
                "GitHub tree response is truncated; pass a narrower branch/ref or clone-based crawler."
            )
        tree = payload.get("tree")
        if not isinstance(tree, list):
            raise GitHubCrawlerError(f"GitHub tree response missing tree list: {owner}/{repo}@{branch}")
        return list(tree)

    def _raw(self, owner: str, repo: str, branch: str, path: str) -> str:
        raw_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
        url = f"{RAW_GITHUB}/{owner}/{repo}/{urllib.parse.quote(branch)}/{raw_path}"
        data = self._request(url)
        return data.decode("utf-8")

    def _json(self, url: str) -> Dict:
        data = self._request(url)
        return json.loads(data.decode("utf-8"))

    def _request(self, url: str) -> bytes:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self._user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GitHubCrawlerError(f"GitHub request failed: {exc.code} {url} {detail}") from exc
        except urllib.error.URLError as exc:
            raise GitHubCrawlerError(f"GitHub request failed: {url} {exc}") from exc

    @staticmethod
    def _document(
        owner: str,
        repo: str,
        branch: str,
        path: str,
        text: str,
        item: Dict,
        topic: str,
        source_type: str,
    ) -> Dict:
        stem = Path(path).stem
        source_url = f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"
        raw_url = f"{RAW_GITHUB}/{owner}/{repo}/{branch}/{path}"
        return {
            "title": stem,
            "heading_path": path,
            "text": text,
            "topic": topic,
            "source_type": source_type,
            "source_url": source_url,
            "metadata": {
                "github_owner": owner,
                "github_repo": repo,
                "github_branch": branch,
                "github_path": path,
                "github_sha": item.get("sha", ""),
                "github_size": item.get("size"),
                "raw_url": raw_url,
            },
        }


def _iter_jsonl(path: Path) -> Iterable[Dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Crawl all .md files from a GitHub repo into JSONL")
    parser.add_argument(
        "--repo",
        default="https://github.com/liuup/claude-code-analysis",
        help="GitHub repo URL or owner/name",
    )
    parser.add_argument("--branch", help="Branch/ref to crawl; defaults to repo default_branch")
    parser.add_argument("--out", help="Output JSONL path")
    parser.add_argument("--raw-out-dir", help="Directory for raw Markdown files; preserves repo paths")
    parser.add_argument("--limit", type=int, help="Maximum number of Markdown files to crawl")
    parser.add_argument("--topic", default="claude-code-analysis", help="Topic stored for PG filters")
    parser.add_argument("--source-type", default="doc", help="source_type stored for PG ingestion")
    parser.add_argument("--token-env", default="GITHUB_TOKEN", help="Env var containing a GitHub token")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--pause-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)

    owner, name = parse_github_repo(args.repo)
    token = os.environ.get(args.token_env) if args.token_env else None
    crawler = GitHubMarkdownCrawler(
        token=token,
        timeout=args.timeout,
        pause_seconds=args.pause_seconds,
    )
    if args.raw_out_dir:
        paths = crawler.write_raw_markdown(
            args.repo,
            out_dir=Path(args.raw_out_dir),
            branch=args.branch,
            topic=args.topic,
            source_type=args.source_type,
            limit=args.limit,
        )
        print(f"[github-crawler] wrote {len(paths)} raw markdown files to {args.raw_out_dir}")
        for path in paths:
            print(path)

    if args.out or not args.raw_out_dir:
        out = Path(args.out) if args.out else default_output_path(owner, name)
        count = crawler.write_jsonl(
            args.repo,
            out=out,
            branch=args.branch,
            topic=args.topic,
            source_type=args.source_type,
            limit=args.limit,
        )
        print(f"[github-crawler] wrote {count} markdown documents to {out}")


if __name__ == "__main__":
    main()
