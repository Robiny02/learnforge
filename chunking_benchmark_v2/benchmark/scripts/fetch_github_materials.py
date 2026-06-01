"""
Fetch Markdown files from https://github.com/liuup/claude-code-analysis.

This script is intentionally optional. It keeps the benchmark package small and lets
Claude Code fetch the newest public Markdown files when running locally.
"""

from pathlib import Path
import json
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw" / "github_md"
REPO = "https://github.com/liuup/claude-code-analysis.git"

def run(cmd, cwd=None):
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = ROOT / ".tmp_claude_code_analysis"
    if tmp.exists():
        shutil.rmtree(tmp)

    try:
        run(["git", "clone", "--depth", "1", REPO, str(tmp)])
    except Exception as e:
        print(f"[WARN] git clone failed: {e}")
        print("Please manually place Markdown files from the repo into benchmark/data/raw/github_md/")
        sys.exit(0)

    copied = []
    for pattern in ["README.md", "analysis/*.md"]:
        for p in tmp.glob(pattern):
            target = OUT / p.name
            shutil.copy2(p, target)
            copied.append(str(target.relative_to(ROOT)))

    manifest = {
        "source_repo": REPO,
        "copied_count": len(copied),
        "files": copied,
        "note": "These are public Markdown materials from the claude-code-analysis repository."
    }
    (OUT / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    shutil.rmtree(tmp)
    print(f"Copied {len(copied)} Markdown files into {OUT}")

if __name__ == "__main__":
    main()
