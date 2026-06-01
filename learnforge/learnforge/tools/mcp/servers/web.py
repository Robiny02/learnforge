"""Built-in Web Fetch MCP server."""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List

from ._common import json_response, schema, serve

REPO_ROOT = Path(__file__).resolve().parents[4]
ARTIFACT_DIR = REPO_ROOT / "docs" / "reports" / "fetched"


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in {"p", "br", "div", "section", "article", "li", "h1", "h2", "h3", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in {"p", "li", "h1", "h2", "h3", "pre"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        text = html.unescape(data).strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        if self._skip:
            return
        self.parts.append(text + " ")


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^\w一-鿿-]+", "-", text).strip("-").lower()
    return (cleaned[:80] or "web-fetch")


def _compact(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_url(args: Dict[str, Any]) -> Dict[str, Any]:
    url = str(args.get("url") or "")
    max_chars = max(500, min(int(args.get("max_chars") or 12000), 50000))
    save = bool(args.get("save", True))
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return json_response({"error": "url must start with http:// or https://"}, is_error=True)
    req = urllib.request.Request(url, headers={"User-Agent": "LearnForge-MCP/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            content_type = resp.headers.get("content-type", "")
            raw = resp.read(3_000_000)
    except urllib.error.HTTPError as exc:
        return json_response({"error": f"HTTP {exc.code}: {exc.reason}"}, is_error=True)
    except Exception as exc:  # noqa: BLE001
        return json_response({"error": f"{type(exc).__name__}: {exc}"}, is_error=True)

    body = raw.decode("utf-8", errors="replace")
    title = ""
    if "html" in content_type.lower() or "<html" in body[:500].lower():
        parser = TextExtractor()
        parser.feed(body)
        title = _compact(parser.title)
        text = _compact("".join(parser.parts))
    else:
        text = _compact(body)
    title = title or parsed.netloc + parsed.path
    markdown = f"# {title}\n\nSource: {url}\nFetched: {datetime.now(timezone.utc).isoformat()}\n\n{text}"
    artifact_path = ""
    if save:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{_slug(title)}.md"
        out = ARTIFACT_DIR / fname
        out.write_text(markdown, encoding="utf-8")
        artifact_path = str(out)
    return json_response({
        "url": url,
        "title": title,
        "content_type": content_type,
        "artifact_path": artifact_path,
        "markdown": markdown[:max_chars],
        "truncated": len(markdown) > max_chars,
    })


TOOLS = [
    {
        "name": "fetch_url",
        "description": "Fetch one http/https URL, extract readable text, and optionally save a markdown artifact.",
        "inputSchema": schema({
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "default": 12000},
            "save": {"type": "boolean", "default": True},
        }, ["url"]),
    }
]

HANDLERS = {"fetch_url": fetch_url}


if __name__ == "__main__":
    serve("learnforge-web", TOOLS, HANDLERS)
