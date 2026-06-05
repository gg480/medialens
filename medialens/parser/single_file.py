"""
SingleFileParser - 单文件解析器

使用 guessit 解析常规视频文件（.mkv/.mp4/.avi 等）。
将 guessit 的输出字段映射到 ParsedMedia 数据结构。
"""

from __future__ import annotations

from pathlib import Path

from guessit import guessit

from medialens.models import FileFormat, ParsedMedia
from medialens.parser.base import BaseParser


class SingleFileParser(BaseParser):
    """单文件解析器，使用 guessit 提取媒体信息"""

    # guessit 字段名 → ParsedMedia 字段名映射
    FIELD_MAP = {
        "title": "title",
        "year": "year",
        "season": "season",
        "episode": "episode",
        "source": "source",
        "screen_size": "resolution",
        "video_codec": "video_codec",
        "audio_codec": "audio_codec",
        "release_group": "release_group",
    }

    def can_handle(self, file_format: FileFormat) -> bool:
        return file_format in (
            FileFormat.SINGLE_FILE,
            FileFormat.TV_SEASON,
        )

    def parse(self, path: str) -> ParsedMedia:
        raw_path = self._make_path(path)
        filename = raw_path.name

        # 调用 guessit 进行解析
        guess = guessit(filename)

        # 构建 ParsedMedia
        result = ParsedMedia(
            raw_path=raw_path,
            raw_filename=filename,
            file_format=FileFormat.SINGLE_FILE,
        )

        # 映射 guessit 字段
        for guess_field, result_field in self.FIELD_MAP.items():
            value = guess.get(guess_field)
            if value is not None:
                setattr(result, result_field, value)

        # 处理 guessit 返回的多集 episode 列表（如 S01E01-E03 → [1, 2, 3]）
        if isinstance(result.episode, list):
            episodes = sorted(result.episode)
            result.episode = episodes[0]
            result.episode_count = len(episodes)

        # 标准化 source 值（Blu-ray → BluRay）
        result.source = self._normalize_source(result.source)

        # 如果 guessit 没有提取到标题，使用清理后的文件名
        if not result.title:
            result.title = self._clean_title(filename)

        # 如果 guessit 没有提取到年份，尝试从文件名提取
        if not result.year:
            result.year = self._extract_year(filename)

        # 判断是否剧集
        if result.season is not None or result.episode is not None:
            result.is_episode = True
            result.file_format = FileFormat.TV_EPISODE

        # 计算置信度
        result.confidence = self._calc_confidence(guess)

        return result

    def _calc_confidence(self, guess: dict) -> float:
        """根据 guessit 结果计算解析置信度"""
        score = 0.0
        if guess.get("title"):
            score += 0.4
        if guess.get("year"):
            score += 0.2
        if guess.get("source"):
            score += 0.15
        if guess.get("screen_size"):
            score += 0.15
        if guess.get("video_codec") or guess.get("audio_codec"):
            score += 0.1
        return min(score, 1.0)
