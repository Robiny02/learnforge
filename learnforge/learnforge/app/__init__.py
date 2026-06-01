"""触发/接入层（trigger）：FastAPI 应用 + Web UI。

`uvicorn learnforge.app:api` 仍可用——这里把 server.py 的 api 暴露到包级。
"""
from .server import api  # noqa: F401
