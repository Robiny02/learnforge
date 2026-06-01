"""Built-in GitHub read-only MCP server."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Tuple

from ._common import json_response, schema, serve

API = "https://api.github.com"


def _headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "LearnForge-MCP/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_repo(value: str) -> Tuple[str, str]:
    value = value.strip().rstrip("/")
    if value.startswith("http"):
        parsed = urllib.parse.urlparse(value)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1]
    parts = value.split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    raise ValueError("repo must be 'owner/name' or a GitHub URL")


def _request(path: str) -> Dict[str, Any]:
    req = urllib.request.Request(f"{API}{path}", headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        return {"error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def repo_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        owner, repo = _parse_repo(str(args.get("repo") or args.get("url") or ""))
    except ValueError as exc:
        return json_response({"error": str(exc)}, is_error=True)
    info = _request(f"/repos/{owner}/{repo}")
    if info.get("error"):
        return json_response(info, is_error=True)
    languages = _request(f"/repos/{owner}/{repo}/languages")
    readme = _request(f"/repos/{owner}/{repo}/readme")
    readme_text = ""
    if readme.get("content"):
        try:
            readme_text = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
        except Exception:
            readme_text = ""
    return json_response({
        "repo": f"{owner}/{repo}",
        "description": info.get("description"),
        "stars": info.get("stargazers_count"),
        "forks": info.get("forks_count"),
        "open_issues": info.get("open_issues_count"),
        "default_branch": info.get("default_branch"),
        "license": (info.get("license") or {}).get("spdx_id"),
        "topics": info.get("topics") or [],
        "languages": languages if not languages.get("error") else {},
        "readme_excerpt": readme_text[:6000],
        "html_url": info.get("html_url"),
    })


def list_tree(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        owner, repo = _parse_repo(str(args.get("repo") or ""))
    except ValueError as exc:
        return json_response({"error": str(exc)}, is_error=True)
    ref = str(args.get("ref") or "HEAD")
    recursive = bool(args.get("recursive", False))
    suffix = "?recursive=1" if recursive else ""
    data = _request(f"/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(ref, safe='')}{suffix}")
    return json_response(data, is_error=bool(data.get("error")))


def read_file(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        owner, repo = _parse_repo(str(args.get("repo") or ""))
    except ValueError as exc:
        return json_response({"error": str(exc)}, is_error=True)
    path = str(args.get("path") or "")
    ref = str(args.get("ref") or "")
    if not path:
        return json_response({"error": "path is required"}, is_error=True)
    query = f"?ref={urllib.parse.quote(ref)}" if ref else ""
    data = _request(f"/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}{query}")
    if data.get("error"):
        return json_response(data, is_error=True)
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    else:
        content = ""
    return json_response({
        "repo": f"{owner}/{repo}",
        "path": path,
        "sha": data.get("sha"),
        "size": data.get("size"),
        "download_url": data.get("download_url"),
        "content": content[:50000],
        "truncated": len(content) > 50000,
    })


TOOLS = [
    {
        "name": "repo_summary",
        "description": "Read public GitHub repository metadata, languages, and README excerpt.",
        "inputSchema": schema({"repo": {"type": "string"}, "url": {"type": "string"}}, []),
    },
    {
        "name": "list_tree",
        "description": "List a GitHub repository tree at a ref.",
        "inputSchema": schema({
            "repo": {"type": "string"},
            "ref": {"type": "string", "default": "HEAD"},
            "recursive": {"type": "boolean", "default": False},
        }, ["repo"]),
    },
    {
        "name": "read_file",
        "description": "Read one text file from a GitHub repository.",
        "inputSchema": schema({
            "repo": {"type": "string"},
            "path": {"type": "string"},
            "ref": {"type": "string"},
        }, ["repo", "path"]),
    },
]

HANDLERS = {"repo_summary": repo_summary, "list_tree": list_tree, "read_file": read_file}


if __name__ == "__main__":
    serve("learnforge-github", TOOLS, HANDLERS)
