from __future__ import annotations

import pytest

from learnforge.knowledge.github_markdown_crawler import (
    GitHubMarkdownCrawler,
    is_markdown_path,
    parse_github_repo,
)


class FakeCrawler(GitHubMarkdownCrawler):
    def crawl(self, repo, branch=None, topic="", source_type="doc", limit=None):
        docs = [
            {
                "title": "README",
                "heading_path": "README.md",
                "text": "# README\n\nraw",
                "topic": topic,
                "source_type": source_type,
                "source_url": "https://example.test/README.md",
                "metadata": {"github_path": "README.md"},
            },
            {
                "title": "intro",
                "heading_path": "docs/intro.md",
                "text": "# Intro\n\nraw",
                "topic": topic,
                "source_type": source_type,
                "source_url": "https://example.test/docs/intro.md",
                "metadata": {"github_path": "docs/intro.md"},
            },
        ]
        yield from docs[:limit]


def test_parse_github_repo_url_and_slug():
    assert parse_github_repo("https://github.com/liuup/claude-code-analysis") == (
        "liuup",
        "claude-code-analysis",
    )
    assert parse_github_repo("liuup/claude-code-analysis.git") == (
        "liuup",
        "claude-code-analysis",
    )


def test_parse_github_repo_rejects_invalid_value():
    with pytest.raises(ValueError):
        parse_github_repo("https://example.com/liuup/claude-code-analysis")


def test_is_markdown_path_only_matches_md_files():
    assert is_markdown_path("README.md")
    assert is_markdown_path("docs/ARCHITECTURE.MD")
    assert not is_markdown_path("docs/notes.markdown")
    assert not is_markdown_path("src/main.py")


def test_document_shape_matches_postgres_jsonl_contract():
    doc = GitHubMarkdownCrawler._document(
        owner="liuup",
        repo="claude-code-analysis",
        branch="main",
        path="docs/intro.md",
        text="# Intro\n\nraw markdown",
        item={"sha": "abc123", "size": 21},
        topic="claude-code-analysis",
        source_type="doc",
    )

    assert doc["title"] == "intro"
    assert doc["heading_path"] == "docs/intro.md"
    assert doc["text"] == "# Intro\n\nraw markdown"
    assert doc["topic"] == "claude-code-analysis"
    assert doc["source_type"] == "doc"
    assert doc["source_url"] == (
        "https://github.com/liuup/claude-code-analysis/blob/main/docs/intro.md"
    )
    assert doc["metadata"]["github_path"] == "docs/intro.md"
    assert doc["metadata"]["github_sha"] == "abc123"


def test_write_raw_markdown_preserves_original_text(tmp_path):
    crawler = FakeCrawler()
    paths = crawler.write_raw_markdown(
        "liuup/claude-code-analysis",
        out_dir=tmp_path,
        limit=1,
    )

    assert paths == [tmp_path / "README.md"]
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "# README\n\nraw"
