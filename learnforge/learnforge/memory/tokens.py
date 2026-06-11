"""Token 计数 —— 上下文压缩的度量基元。

精确路径用 tiktoken（cl100k_base，覆盖 GPT/Claude 量级足够近似）；缺包或编码异常时
回退**纯字符估算**，保持"链路永远通/离线确定"哲学（tiktoken 是可选依赖，当前环境常无）。

估算规则：CJK 字符约 1 token/字，其余（拉丁/数字/标点）约 1 token/4 字符。混合文本分别统计。
误差对"按预算裁剪"足够——宁可略高估、早一点裁，也不要塞爆窗口。
"""

from __future__ import annotations

from functools import lru_cache

_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _encoder():
    """惰性加载 tiktoken 编码器；缺包/失败 → None（调用方回退估算）。只试一次。"""
    try:
        import tiktoken

        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:
        return None


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF      # CJK 统一表意
        or 0x3040 <= cp <= 0x30FF   # 日文假名
        or 0x3000 <= cp <= 0x303F   # CJK 标点
        or 0xFF00 <= cp <= 0xFFEF   # 全角
        or 0xAC00 <= cp <= 0xD7A3   # 谚文
    )


def _estimate(text: str) -> int:
    """无 tiktoken 时的字符估算：CJK 1:1，其余 4 字符≈1 token，向上取整。"""
    cjk = sum(1 for c in text if _is_cjk(c))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def count_tokens(text: str) -> int:
    """文本 token 数。tiktoken 可用则精确，否则字符估算。空串 → 0。"""
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return _estimate(text)


def truncate_to_tokens(text: str, max_tokens: int, marker: str = "…〔上下文已截断〕") -> str:
    """把 text 硬截断到 ≤max_tokens（含 marker 余量）。已达标则原样返回。

    用比例估算定初始保留字符数，再收紧直到 token 达标——对 tiktoken / 估算两条路都稳。
    """
    if max_tokens <= 0:
        return ""
    total = count_tokens(text)
    if total <= max_tokens:
        return text
    marker_tok = count_tokens(marker)
    budget = max(0, max_tokens - marker_tok)
    if budget == 0:
        return marker
    keep = max(1, int(len(text) * budget / max(total, 1)))
    # 收紧：估算偏低时逐步回退，避免超预算
    while keep > 1 and count_tokens(text[:keep]) > budget:
        keep = int(keep * 0.9)
    # 放宽：估算偏高时尽量多留（不超预算）
    while keep < len(text) and count_tokens(text[: keep + 1]) <= budget:
        keep += 1
    return text[:keep].rstrip() + marker
