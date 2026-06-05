"""
Parser 层抽象基类

定义所有解析器的统一接口：
- parse(path) -> ParsedMedia: 解析路径，返回结构化媒体信息
- can_handle(format) -> bool: 判断是否能处理该格式

提供共享工具方法：
- _clean_title: 清理标题杂质（年份/格式标记/括号等）
- _extract_year: 从文本中提取年份
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from medialens.models import FileFormat, ParsedMedia


class BaseParser(ABC):
    """解析器抽象基类"""

    # 常见质量标签（不区分大小写），按优先级从高到低排列
    QUALITY_TAGS = [
        r"BluRay",
        r"REMUX",
        r"WEB-DL",
        r"WEBRip",
        r"WEB\.DL",
        r"WEB\.Rip",
        r"HDTV",
        r"DVD(Rip|scr|9)",
        r"BDRip",
        r"BRRip",
        r"DVDRip",
        r"HDRip",
        r"HC",
    ]

    # 常见分辨率标签
    RESOLUTION_TAGS = [
        r"2160p",
        r"1080p",
        r"720p",
        r"480p",
        r"4K",
        r"UHD",
        r"8K",
    ]

    # 常见编码标签
    CODEC_TAGS = [
        r"x264",
        r"x265",
        r"h\.?264",
        r"h\.?265",
        r"HEVC",
        r"AVC",
        r"AV1",
        r"VC-?1",
        r"MPEG-?4",
        r"XviD",
        r"DivX",
    ]

    # 常见 HDR / 音效标签
    MISC_TAGS = [
        r"HDR10(\+)?",
        r"Dolby\s*Vision",
        r"DV",
        r"Atmos",
        r"DTS(-HD)?(\s*MA)?",
        r"TrueHD",
        r"FLAC",
        r"DD(\+|P)?(\s*5\.1)?",
        r"AAC(\s*2\.0)?",
        r"iNTERNAL",
        r"EXTENDED",
        r"REPACK",
        r"PROPER",
        r"AMZN",
        r"NF",
        r"DSNP",
        r"iTunes",
    ]

    @abstractmethod
    def parse(self, path: str) -> ParsedMedia:
        """解析文件路径，返回结构化媒体信息"""
        raise NotImplementedError

    @abstractmethod
    def can_handle(self, file_format: FileFormat) -> bool:
        """判断是否能处理该格式"""
        raise NotImplementedError

    # guessit 不统一 source 值的映射表
    SOURCE_NORMALIZE = {
        "blu-ray": "BluRay",
        "bluray": "BluRay",
        "web-dl": "WEB-DL",
        "webdl": "WEB-DL",
        "web rip": "WEBRip",
        "webrip": "WEBRip",
        "hdtv": "HDTV",
        "dvd": "DVD",
        "dvdrip": "DVDRip",
        "bdrip": "BDRip",
        "brrip": "BRRip",
    }

    # ─── 共享工具方法 ────────────────────────────────────────────────

    def _clean_title(self, raw: str) -> str:
        """
        清理标题中的杂质。

        处理顺序：
        1. 去除文件扩展名
        2. 去除年份（括号/方括号/点分隔）
        3. 去除所有已知质量/分辨率/编码标签
        4. 将分隔符（. _ -）统一替换为空格
        5. 去除多余空白
        """
        text = raw.strip()

        # 1. 去除文件扩展名（最后一个点之后的内容）
        #    只去除常见视频扩展名
        ext_match = re.search(r"\.(mkv|mp4|avi|ts|m2ts|iso|bdmv|bdiso)$", text, re.I)
        if ext_match:
            text = text[: ext_match.start()]

        # 2. 去除年份标记： (2004) [2004] .2004
        text = re.sub(
            r"[\s\.\(\{\[]*(19[0-9]{2}|20[0-4][0-9]|2050)[\s\.\)\}\]]*",
            " ",
            text,
        )

        # 3. 组合所有标签模式
        all_tags = (
            self.QUALITY_TAGS
            + self.RESOLUTION_TAGS
            + self.CODEC_TAGS
            + self.MISC_TAGS
        )
        tag_pattern = r"[\s\.\-]*(?:" + "|".join(all_tags) + r")[\s\.\-]*"
        text = re.sub(tag_pattern, " ", text, flags=re.I)

        # 4. 将分隔符替换为空格
        text = re.sub(r"[\._\[\]\(\)\{\}]", " ", text)

        # 5. 合并多个空格并去除首尾空白
        text = re.sub(r"\s+", " ", text).strip()

        # 6. 如果清理后为空，返回原始输入
        return text if text else raw.strip()

    def _extract_year(self, text: str) -> Optional[int]:
        """
        从文本中提取年份（1900-2050 范围的四位数字）。

        优先匹配括号/方括号包裹的年份，再匹配松散年份。
        返回第一个匹配的年份，或 None。
        """
        # 先找 (2004) / [2004] / {2004} 这类强包裹的年份
        for m in re.finditer(
            r"(?:\(|\[|\{|\'|\")(19[0-9]{2}|20[0-4][0-9]|2050)(?:\)|\]|\}|\'|\")",
            text,
        ):
            year = int(m.group(1))
            if 1900 <= year <= 2050:
                return year

        # 再找以 . - _ 或空格分隔的四位数字
        for m in re.finditer(
            r"(?:^|[\s\.\-_]+)(19[0-9]{2}|20[0-4][0-9]|2050)(?:$|[\s\.\-_]+)",
            text,
        ):
            year = int(m.group(1))
            if 1900 <= year <= 2050:
                return year

        return None

    def _normalize_source(self, raw_source: Optional[str]) -> Optional[str]:
        """
        标准化 source 值。

        guessit 可能输出 "Blu-ray"、"Bluray" 等不同变体，
        统一为规范写法 "BluRay"、"WEB-DL" 等。
        """
        if not raw_source:
            return raw_source
        key = raw_source.strip().lower()
        return self.SOURCE_NORMALIZE.get(key, raw_source)

    def _make_path(self, path: str) -> Path:
        """安全地将字符串转为 Path 对象"""
        return Path(path).resolve()
