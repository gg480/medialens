"""
MediaLens 多态解析器层

提供四种解析策略：
- SingleFileParser: 单文件 (guessit)
- BlurayParser: 蓝光原盘 BDMV 目录
- ISOParser: ISO/BDISO 镜像文件
- TVEpisodeParser: 电视剧集
"""

from medialens.parser.base import BaseParser
from medialens.parser.bluray import BlurayParser
from medialens.parser.iso_parser import ISOParser
from medialens.parser.parser_factory import ParserFactory
from medialens.parser.single_file import SingleFileParser
from medialens.parser.tv_episode import TVEpisodeParser

__all__ = [
    "BaseParser",
    "SingleFileParser",
    "BlurayParser",
    "ISOParser",
    "TVEpisodeParser",
    "ParserFactory",
]
