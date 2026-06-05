"""
TVEpisodeParser - 电视剧集解析器

使用 guessit 解析电视剧单集文件，与 SingleFileParser 类似，
但更强调 season/episode 信息，并支持多集合并检测。
"""

from __future__ import annotations

import re

from guessit import guessit

from medialens.models import FileFormat, ParsedMedia
from medialens.parser.base import BaseParser


class TVEpisodeParser(BaseParser):
    """电视剧集解析器"""

    def can_handle(self, file_format: FileFormat) -> bool:
        return file_format == FileFormat.TV_EPISODE

    def parse(self, path: str) -> ParsedMedia:
        raw_path = self._make_path(path)
        filename = raw_path.name

        guess = guessit(filename)

        title = guess.get("title") or self._clean_title(filename)
        year = guess.get("year") or self._extract_year(filename)
        season = guess.get("season")
        episode = guess.get("episode")

        # 处理 guessit 返回的多集 episode 列表（如 S01E01-E03 → [1, 2, 3]）
        episode_count: int | None = None
        if isinstance(episode, list):
            episodes = sorted(episode)
            episode = episodes[0]
            episode_count = len(episodes)
        else:
            # 检测多集合并（如 S01E01-E03 或 S01E01-03）
            episode_count = self._detect_multi_episode(filename, guess)

        result = ParsedMedia(
            raw_path=raw_path,
            raw_filename=filename,
            file_format=FileFormat.TV_EPISODE,
            title=title,
            year=year,
            season=season,
            episode=episode,
            episode_count=episode_count,
            source=self._normalize_source(guess.get("source")),
            resolution=guess.get("screen_size"),
            video_codec=guess.get("video_codec"),
            audio_codec=guess.get("audio_codec"),
            release_group=guess.get("release_group"),
            is_episode=True,
            confidence=self._calc_confidence(guess),
        )

        return result

    def _detect_multi_episode(self, filename: str, guess: dict) -> int | None:
        """
        检测多集合并。

        支持模式：
        - S01E01-E03 → count = 3
        - S01E01-03  → count = 3
        - S01E01E02  → count = 2
        - 1x01-03    → count = 3

        返回总集数，如果非多集则返回 None。
        """
        episode = guess.get("episode")
        if episode is None:
            return None

        # 模式1: S01E01-E03 — "E" + 数字 + "-" + "E" + 数字
        m = re.search(
            r"[Ee](\d{1,3})\s*[-–]\s*[Ee](\d{1,3})",
            filename,
        )
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if end > start:
                return end - start + 1

        # 模式2: S01E01-03 — "E" + 数字 + "-" + 数字（无 E 前缀）
        m = re.search(
            r"[Ee](\d{1,3})\s*[-–]\s*(\d{1,3})",
            filename,
        )
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if end > start:
                return end - start + 1

        # 模式3: S01E01E02 — 连续 E 标记
        m = re.search(
            r"[Ee](\d{1,3})[Ee](\d{1,3})",
            filename,
        )
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if end > start:
                return end - start + 1

        # 模式4: 1x01-03
        m = re.search(
            r"(\d+)x(\d{1,3})\s*[-–]\s*(\d{1,3})",
            filename,
        )
        if m:
            start, end = int(m.group(2)), int(m.group(3))
            if end > start:
                return end - start + 1

        return None

    def _calc_confidence(self, guess: dict) -> float:
        """计算剧集解析置信度"""
        score = 0.0
        if guess.get("title"):
            score += 0.3
        if guess.get("season") is not None:
            score += 0.25
        if guess.get("episode") is not None:
            score += 0.25
        if guess.get("source"):
            score += 0.1
        if guess.get("screen_size"):
            score += 0.1
        return min(score, 1.0)
