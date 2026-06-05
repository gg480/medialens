"""
MediaLens API 启动入口

使用方式：
    python -m medialens.api.run

或者：
    uvicorn medialens.api.server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "medialens.api.server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
