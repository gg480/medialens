"""
ISOParser - ISO/BDISO 镜像文件解析器

处理 ISO 镜像文件（.iso / .bdmv / .bdiso）。
从文件名（无扩展名）提取标题信息，使用 guessit 辅助解析。
"""

from __future__ import annotations

from pathlib import Path

from guessit import guessit

from medialens.models import FileFormat, ParsedMedia
from medialens.parser.base import BaseParser


class ISOParser(BaseParser):
    """ISO/BDISO 镜像文件解析器"""

    def can_handle(self, file_format: FileFormat) -> bool:
        return file_format == FileFormat.BDISO

    def parse(self, path: str) -> ParsedMedia:
        raw_path = self._make_path(path)
        filename = raw_path.name

        # 去除扩展名后的基名（用于标题提取）
        stem = raw_path.stem

        # 尝试用 guessit 解析（会输出大部分元数据）
        guess = guessit(filename)

        # 优先用 guessit 的 title，否则用 stem 清理
        title = guess.get("title")
        if not title:
            title = self._clean_title(stem)

        year = guess.get("year") or self._extract_year(stem)
        raw_source = guess.get("source")
        source = self._normalize_source(raw_source) or "BluRay"

        result = ParsedMedia(
            raw_path=raw_path,
            raw_filename=filename,
            file_format=FileFormat.BDISO,
            title=title,
            year=year,
            source=source,
            resolution=guess.get("screen_size"),
            video_codec=guess.get("video_codec"),
            audio_codec=guess.get("audio_codec"),
            release_group=guess.get("release_group"),
            confidence=self._calc_confidence(guess, title),
        )

        return result

    def _calc_confidence(self, guess: dict, title: str) -> float:
        """计算 ISO 解析置信度"""
        score = 0.0
        if guess.get("title") or title:
            score += 0.5
        if guess.get("year"):
            score += 0.2
        if guess.get("source"):
            score += 0.15
        if guess.get("screen_size"):
            score += 0.15
        return min(score, 1.0)
