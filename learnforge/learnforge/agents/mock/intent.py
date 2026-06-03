"""兼容 shim：面试内回合意图分类已迁至统一意图层 `learnforge.intent.mock_turn`。

保留此模块仅为不破坏历史 import（app/server.py、tests/test_mock_visuals.py 等用
`from learnforge.agents.mock.intent import classify_mock_input, ...`）。
新代码请用 `from ...intent.mock_turn import ...`。
"""

from __future__ import annotations

from ...intent.mock_turn import (  # noqa: F401
    ExitConfirm,
    MockIntent,
    classify_exit_confirmation,
    classify_mock_input,
    fast_kind,
    interrupt_for,
)
