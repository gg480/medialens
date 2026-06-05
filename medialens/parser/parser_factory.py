"""
ParserFactory - 解析器工厂

根据 FileFormat 类型返回合适的 Parser 实例。
集中管理所有解析器的注册和生命周期。
"""

from __future__ import annotations

from medialens.models import FileFormat, ParsedMedia
from medialens.parser.base import BaseParser
from medialens.parser.bluray import BlurayParser
from medialens.parser.iso_parser import ISOParser
from medialens.parser.single_file import SingleFileParser
from medialens.parser.tv_episode import TVEpisodeParser


class ParserFactory:
    """
    解析器工厂。

    根据 FileFormat 返回对应的解析器实例。
    支持运行时注册自定义解析器。
    """

    def __init__(self):
        self._parsers: dict[FileFormat, BaseParser] = {
            FileFormat.SINGLE_FILE: SingleFileParser(),
            FileFormat.BLURAY_BDMV: BlurayParser(),
            FileFormat.BDISO: ISOParser(),
            FileFormat.TV_EPISODE: TVEpisodeParser(),
            FileFormat.TV_SEASON: SingleFileParser(),  # 暂用 SingleFileParser 处理季目录
        }

    def get_parser(self, file_format: FileFormat) -> BaseParser:
        """
        获取对应格式的解析器。

        参数:
            file_format: 文件格式类型

        返回:
            对应的解析器实例

        异常:
            ValueError: 如果该格式没有注册解析器
        """
        parser = self._parsers.get(file_format)
        if parser is None:
            raise ValueError(f"没有为 {file_format.value} 注册解析器")
        return parser

    def parse(self, path: str, file_format: FileFormat) -> ParsedMedia:
        """
        根据格式解析文件。

        便捷方法，相当于 get_parser(format).parse(path)。

        参数:
            path: 文件路径
            file_format: 文件格式类型

        返回:
            解析后的媒体信息
        """
        parser = self.get_parser(file_format)
        return parser.parse(path)

    def register_parser(
        self,
        file_format: FileFormat,
        parser: BaseParser,
    ) -> None:
        """
        注册自定义解析器（覆盖默认）。

        参数:
            file_format: 文件格式类型
            parser: 解析器实例
        """
        self._parsers[file_format] = parser

    @property
    def supported_formats(self) -> list[FileFormat]:
        """返回所有已注册的格式"""
        return list(self._parsers.keys())
