"""
图片下载器

独立的 HTTP 图片下载模块，负责从 TMDB 等远程源下载图片到本地。
使用 httpx 作为 HTTP 客户端，支持超时和错误处理。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# 默认超时时间（秒）
_DEFAULT_TIMEOUT = 30
# 下载块大小（字节）
_CHUNK_SIZE = 8192


class ImageDownloadError(Exception):
    """图片下载失败时抛出的异常。"""


class ImageDownloader:
    """图片下载器。

    职责：
    - 从远程 URL 下载图片到本地文件
    - 超时和重试处理
    - 自动创建输出目录
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        """初始化下载器。

        Args:
            timeout: HTTP 请求超时时间（秒）
        """
        self.timeout = timeout

    def download(
        self,
        url: str,
        output_path: str,
        overwrite: bool = False,
    ) -> str:
        """下载单个图片文件。

        Args:
            url: 远程图片 URL
            output_path: 本地输出路径
            overwrite: 是否覆盖已存在的文件

        Returns:
            下载文件的绝对路径

        Raises:
            ImageDownloadError: 下载失败时抛出
        """
        if not url:
            raise ImageDownloadError("URL 为空")

        abs_path = os.path.abspath(output_path)

        # 文件已存在且不覆盖
        if os.path.exists(abs_path) and not overwrite:
            logger.debug("图片已存在，跳过下载: %s", abs_path)
            return abs_path

        # 创建输出目录
        output_dir = os.path.dirname(abs_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        try:
            with httpx.Client(
                timeout=self.timeout, follow_redirects=True
            ) as client:
                response = client.get(url)
                response.raise_for_status()

                with open(abs_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=_CHUNK_SIZE):
                        f.write(chunk)

            logger.info("图片下载完成: %s", abs_path)
            return abs_path

        except httpx.TimeoutException as e:
            raise ImageDownloadError(
                f"下载超时 ({self.timeout}s): {url}"
            ) from e
        except httpx.HTTPStatusError as e:
            raise ImageDownloadError(
                f"HTTP 错误 {e.response.status_code}: {url}"
            ) from e
        except httpx.RequestError as e:
            raise ImageDownloadError(f"请求失败: {url} - {e}") from e
        except OSError as e:
            raise ImageDownloadError(
                f"文件写入失败: {abs_path} - {e}"
            ) from e

    def download_batch(
        self,
        url_map: dict[str, str],
        output_dir: str,
        overwrite: bool = False,
    ) -> dict[str, str]:
        """批量下载图片。

        Args:
            url_map: 名称到 URL 的映射
            output_dir: 输出目录
            overwrite: 是否覆盖已存在的文件

        Returns:
            名称到本地路径的映射（仅包含下载成功的）
        """
        os.makedirs(output_dir, exist_ok=True)
        results: dict[str, str] = {}

        for name, url in url_map.items():
            output_path = os.path.join(output_dir, name)
            try:
                self.download(url, output_path, overwrite)
                results[name] = output_path
            except ImageDownloadError as e:
                logger.warning("批量下载失败 [%s]: %s", name, e)

        return results
