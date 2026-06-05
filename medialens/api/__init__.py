"""
MediaLens REST API 模块

提供 FastAPI HTTP 接口供前端 SPA 调用。
"""

from medialens.api.server import app

__all__ = ["app"]
