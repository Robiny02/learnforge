"""本地 stdio MCP server + 可直接调用的出图函数：经 OpenRouter 的图像模型生成 PNG。

为什么自写而非接第三方 MCP：完全可控、依赖最少（仅用已安装的 mcp + httpx）、
不引入对陌生 npm/pypi 包的信任成本。只读外部 API、只写本仓库 docs/assets/ 下的图片文件，
不碰任何项目代码或 Manager 写路径。

密钥从仓库根 .env 读取（OPENROUTER_API_KEY，OpenAI 兼容网关）。
- 出图走 OpenRouter chat/completions + 图像模型（默认 openai/gpt-5-image-mini），
  响应里 choices[0].message.images[0].image_url.url 是 data:image/png;base64,... 。

用法：
- 作为 MCP server（由 claude mcp add 调起）：python3.11 scripts/mcp_gpt_image.py
- 作为库：from mcp_gpt_image import generate_image_core
"""

from __future__ import annotations

import base64
import os
import pathlib

import httpx

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS_DIR = REPO_ROOT / "docs" / "assets"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-5-image-mini"

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except Exception:
    pass


def _api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""


def generate_image_core(prompt: str, filename: str = "diagram.png",
                        model: str = DEFAULT_MODEL) -> str:
    """生成一张图并存到 docs/assets/<filename>，返回保存路径或 ERROR 字符串。"""
    key = _api_key()
    if not key:
        return "ERROR: OPENROUTER_API_KEY 未设置。"
    if not filename.lower().endswith(".png"):
        filename += ".png"
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "modalities": ["image", "text"],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=300.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return f"ERROR: {str(data['error'])[:300]}"
        imgs = (data.get("choices") or [{}])[0].get("message", {}).get("images") or []
        if not imgs:
            return f"ERROR: 响应无图像：{str(data)[:200]}"
        url = imgs[0].get("image_url", {}).get("url", "")
        if "base64," in url:
            url = url.split("base64,", 1)[1]
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        out = ASSETS_DIR / filename
        out.write_bytes(base64.b64decode(url))
        return f"已保存：{out}"
    except httpx.HTTPStatusError as e:
        return f"ERROR: OpenRouter {e.response.status_code}：{e.response.text[:300]}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {type(e).__name__}: {e}"


# --- MCP server 封装 ---
def _build_server():
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("gpt-image")

    @server.tool()
    def generate_image(prompt: str, filename: str = "diagram.png",
                       model: str = DEFAULT_MODEL) -> str:
        """用 gpt 图像模型（经 OpenRouter）生成一张图并存到 docs/assets/<filename>。

        Args:
            prompt: 完整英文出图描述（建议用 gpt-image-2-style-library 风格库拼装）。
            filename: 输出 PNG 文件名（kebab-case）。
            model: OpenRouter 图像模型 id，默认 openai/gpt-5-image-mini。
        """
        return generate_image_core(prompt, filename, model)

    return server


if __name__ == "__main__":
    _build_server().run()
