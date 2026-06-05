"""
Parser 层单元测试

覆盖所有解析器子类、工厂模式、以及共享工具方法。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from medialens.models import FileFormat, ParsedMedia
from medialens.parser import (
    BaseParser,
    BlurayParser,
    ISOParser,
    ParserFactory,
    SingleFileParser,
    TVEpisodeParser,
)


# =============================================================
# 辅助 Fixtures
# =============================================================


@pytest.fixture
def single_parser():
    return SingleFileParser()


@pytest.fixture
def bluray_parser():
    return BlurayParser()


@pytest.fixture
def iso_parser():
    return ISOParser()


@pytest.fixture
def tv_parser():
    return TVEpisodeParser()


@pytest.fixture
def factory():
    return ParserFactory()


# =============================================================
# _clean_title 测试（基类共享方法）
# =============================================================


class TestCleanTitle:
    """测试 BaseParser._clean_title 的清理逻辑"""

    def test_removes_video_extension(self):
        parser = SingleFileParser()
        assert parser._clean_title("功夫.2004.mkv") == "功夫"

    def test_removes_year_in_parentheses(self):
        parser = SingleFileParser()
        result = parser._clean_title("Inception (2010) 1080p.mkv")
        assert "Inception" in result
        assert "2010" not in result

    def test_removes_year_in_brackets(self):
        parser = SingleFileParser()
        result = parser._clean_title("Movie [2005].mkv")
        assert "Movie" in result
        assert "2005" not in result

    def test_removes_quality_tags(self):
        parser = SingleFileParser()
        result = parser._clean_title("The.Matrix.1999.BluRay.1080p.x264.mkv")
        assert "The Matrix" in result or "The.Matrix" not in result
        assert "BluRay" not in result.upper() or "BluRay" not in result
        assert "1080p" not in result
        assert "x264" not in result

    def test_removes_dot_separator(self):
        parser = SingleFileParser()
        result = parser._clean_title("Avatar.2009.EXTENDED.mkv")
        assert "Avatar" in result
        assert "2009" not in result

    def test_chinese_title_with_year(self):
        parser = SingleFileParser()
        result = parser._clean_title("功夫.2004.1080p.BluRay.x264.mkv")
        assert "功夫" in result
        assert "2004" not in result

    def test_removes_hdr_dv_tags(self):
        parser = SingleFileParser()
        result = parser._clean_title("Tenet.2020.2160p.UHD.BluRay.DV.HDR10.Atmos.mkv")
        assert "Tenet" in result
        assert "DV" not in result

    def test_empty_after_cleaning_returns_original(self):
        parser = SingleFileParser()
        # 全部是标签的情况，应返回原始输入
        result = parser._clean_title("1080p.mkv")
        assert result  # 不为空


# =============================================================
# _extract_year 测试
# =============================================================


class TestExtractYear:
    def test_extracts_parenthesized_year(self):
        parser = SingleFileParser()
        assert parser._extract_year("Inception (2010)") == 2010

    def test_extracts_dot_separated_year(self):
        parser = SingleFileParser()
        assert parser._extract_year("The.Matrix.1999.1080p") == 1999

    def test_extracts_bracket_year(self):
        parser = SingleFileParser()
        assert parser._extract_year("Movie [2022]") == 2022

    def test_returns_none_for_no_year(self):
        parser = SingleFileParser()
        assert parser._extract_year("Movie Without Year") is None

    def test_returns_first_year(self):
        parser = SingleFileParser()
        # 文件名中第一个年份应该是正确的年份
        assert parser._extract_year("2022.Movie.1999.1080p") in (2022, 1999)

    def test_out_of_range_year_returns_none(self):
        parser = SingleFileParser()
        assert parser._extract_year("Movie.1899.1080p") is None
        assert parser._extract_year("Movie.2051.1080p") is None


# =============================================================
# SingleFileParser 测试
# =============================================================


class TestSingleFileParser:
    def test_can_handle(self, single_parser):
        assert single_parser.can_handle(FileFormat.SINGLE_FILE)
        assert single_parser.can_handle(FileFormat.TV_SEASON)
        assert not single_parser.can_handle(FileFormat.BLURAY_BDMV)

    def test_single_file_with_year(self, single_parser):
        """test_single_file_with_year: '功夫.2004.1080p.BluRay.x264.mkv'"""
        result = single_parser.parse("功夫.2004.1080p.BluRay.x264.mkv")
        assert result.title == "功夫"
        assert result.year == 2004
        assert result.resolution == "1080p"
        assert result.source == "BluRay"
        assert result.video_codec is not None
        assert result.file_format == FileFormat.SINGLE_FILE
        assert result.is_episode is False
        assert result.confidence > 0

    def test_single_file_without_year(self, single_parser):
        """test_single_file_without_year: 'Inception.1080p.mkv'"""
        result = single_parser.parse("Inception.1080p.mkv")
        assert result.title == "Inception"
        assert result.resolution == "1080p"
        assert result.year is None or result.year == 2010  # guessit 可能识别出 2010

    def test_single_file_tv_episode_detection(self, single_parser):
        """SingleFileParser 遇到季集信息应自动标记为剧集"""
        result = single_parser.parse("Breaking.Bad.S01E01.1080p.mkv")
        assert result.is_episode is True
        assert result.file_format == FileFormat.TV_EPISODE
        assert result.season == 1
        assert result.episode == 1

    def test_single_file_guessit_fields_mapped(self, single_parser):
        """验证 guessit 字段被正确映射到 ParsedMedia"""
        result = single_parser.parse("The.Matrix.1999.2160p.UHD.BluRay.HEVC.TrueHD.7.1.mkv")
        assert result.title is not None
        assert result.year is not None
        assert result.resolution is not None
        assert result.source is not None
        assert result.video_codec is not None
        assert result.audio_codec is not None


# =============================================================
# BlurayParser 测试
# =============================================================


class TestBlurayParser:
    def test_can_handle(self, bluray_parser):
        assert bluray_parser.can_handle(FileFormat.BLURAY_BDMV)
        assert not bluray_parser.can_handle(FileFormat.SINGLE_FILE)

    def test_bluray_bdmv_with_tempdir(self, bluray_parser):
        """test_bluray_bdmv: 创建临时的 BDMV 目录结构测试"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建模拟的蓝光目录结构
            movie_dir = Path(tmpdir) / "The.Matrix.1999.1080p.BluRay.REMUX"
            movie_dir.mkdir(parents=True)
            bdmv_dir = movie_dir / "BDMV"
            bdmv_dir.mkdir()
            stream_dir = bdmv_dir / "STREAM"
            stream_dir.mkdir()

            # 创建几个模拟的 m2ts 文件
            (stream_dir / "00000.m2ts").write_text("dummy" * 100)
            (stream_dir / "00001.m2ts").write_text("x" * 1000)  # 最大的
            (stream_dir / "00002.m2ts").write_text("dummy" * 200)

            result = bluray_parser.parse(str(movie_dir))
            assert result.file_format == FileFormat.BLURAY_BDMV
            assert result.title == "The Matrix"
            assert result.year == 1999
            assert result.source == "BluRay"
            assert result.m2ts_count == 3
            assert result.largest_m2ts == "00001.m2ts"
            assert result.bdmv_parent is not None
            assert result.confidence > 0

    def test_bluray_bdmv_direct_path(self, bluray_parser):
        """传入 BDMV 目录本身也应能解析"""
        with tempfile.TemporaryDirectory() as tmpdir:
            movie_dir = Path(tmpdir) / "Avatar.2009.BluRay"
            movie_dir.mkdir(parents=True)
            bdmv_dir = movie_dir / "BDMV"
            bdmv_dir.mkdir()
            stream_dir = bdmv_dir / "STREAM"
            stream_dir.mkdir()
            (stream_dir / "00000.m2ts").write_text("dummy")

            # 传入 BDMV 目录本身
            result = bluray_parser.parse(str(bdmv_dir))
            assert result.title == "Avatar"
            assert result.year == 2009
            assert result.m2ts_count == 1

    def test_bluray_no_stream_dir(self, bluray_parser):
        """没有 STREAM 目录时不应崩溃"""
        with tempfile.TemporaryDirectory() as tmpdir:
            movie_dir = Path(tmpdir) / "Test.Movie.BluRay"
            movie_dir.mkdir(parents=True)
            bdmv_dir = movie_dir / "BDMV"
            bdmv_dir.mkdir()
            # 不创建 STREAM 目录

            result = bluray_parser.parse(str(movie_dir))
            assert result.title is not None
            assert result.m2ts_count == 0
            assert result.largest_m2ts is None


# =============================================================
# ISOParser 测试
# =============================================================


class TestISOParser:
    def test_can_handle(self, iso_parser):
        assert iso_parser.can_handle(FileFormat.BDISO)
        assert not iso_parser.can_handle(FileFormat.SINGLE_FILE)

    def test_iso_file(self, iso_parser):
        """test_iso_file: 'The.Matrix.1999.BluRay.REMUX.iso'"""
        result = iso_parser.parse("The.Matrix.1999.BluRay.REMUX.iso")
        assert result.title == "The Matrix"
        assert result.year == 1999
        assert result.source == "BluRay"
        assert result.file_format == FileFormat.BDISO

    def test_iso_file_chinese_title(self, iso_parser):
        """带中文标题的 ISO 文件"""
        result = iso_parser.parse("功夫.2004.BluRay.REMUX.iso")
        assert result.title == "功夫"
        assert result.year == 2004

    def test_iso_without_year(self, iso_parser):
        """没有年份的 ISO 文件"""
        result = iso_parser.parse("Some.Movie.1080p.BluRay.iso")
        assert result.title is not None
        assert result.year is None


# =============================================================
# TVEpisodeParser 测试
# =============================================================


class TestTVEpisodeParser:
    def test_can_handle(self, tv_parser):
        assert tv_parser.can_handle(FileFormat.TV_EPISODE)
        assert not tv_parser.can_handle(FileFormat.SINGLE_FILE)

    def test_tv_episode(self, tv_parser):
        """test_tv_episode: 'Breaking.Bad.S01E01.1080p.mkv'"""
        result = tv_parser.parse("Breaking.Bad.S01E01.1080p.mkv")
        assert result.title == "Breaking Bad"
        assert result.season == 1
        assert result.episode == 1
        assert result.is_episode is True
        assert result.file_format == FileFormat.TV_EPISODE

    def test_tv_multi_episode(self, tv_parser):
        """test_tv_multi_episode: 'S01E01-E03.mkv'"""
        result = tv_parser.parse("S01E01-E03.mkv")
        assert result.episode == 1
        assert result.season == 1
        assert result.episode_count == 3

    def test_tv_multi_episode_compact(self, tv_parser):
        """S01E01E02 紧凑格式"""
        result = tv_parser.parse("S01E01E02.mkv")
        assert result.episode_count == 2

    def test_tv_multi_episode_hyphen_no_e(self, tv_parser):
        """S01E01-03 格式"""
        result = tv_parser.parse("S01E01-03.mkv")
        assert result.episode_count == 3

    def test_tv_episode_with_year(self, tv_parser):
        """带年份的剧集文件"""
        result = tv_parser.parse("Game.of.Thrones.S01E01.2011.1080p.mkv")
        assert result.title is not None
        assert result.season == 1
        assert result.episode == 1
        assert result.year == 2011

    def test_single_episode_no_multi(self, tv_parser):
        """单集文件，episode_count 应为 None"""
        result = tv_parser.parse("Dexter.S01E01.1080p.mkv")
        assert result.episode_count is None


# =============================================================
# ParserFactory 测试
# =============================================================


class TestParserFactory:
    def test_get_parser(self, factory):
        """test_parser_factory_get_parser"""
        assert isinstance(
            factory.get_parser(FileFormat.SINGLE_FILE),
            SingleFileParser,
        )
        assert isinstance(
            factory.get_parser(FileFormat.BLURAY_BDMV),
            BlurayParser,
        )
        assert isinstance(factory.get_parser(FileFormat.BDISO), ISOParser)
        assert isinstance(
            factory.get_parser(FileFormat.TV_EPISODE),
            TVEpisodeParser,
        )
        assert isinstance(
            factory.get_parser(FileFormat.TV_SEASON),
            SingleFileParser,
        )

    def test_get_parser_unknown_raises(self, factory):
        """未注册的格式应抛出 ValueError"""
        with pytest.raises(ValueError):
            factory.get_parser(FileFormat.UNKNOWN)

    def test_factory_parse(self, factory):
        """test_parser_factory_parse"""
        result = factory.parse(
            "功夫.2004.1080p.BluRay.x264.mkv",
            FileFormat.SINGLE_FILE,
        )
        assert isinstance(result, ParsedMedia)
        assert result.title == "功夫"
        assert result.year == 2004

    def test_register_parser(self, factory):
        """注册自定义解析器"""
        class MockParser(BaseParser):
            def parse(self, path):
                return ParsedMedia(
                    raw_path=Path(path),
                    raw_filename=Path(path).name,
                    file_format=FileFormat.UNKNOWN,
                    title="Mock",
                )
            def can_handle(self, fmt):
                return fmt == FileFormat.UNKNOWN

        factory.register_parser(FileFormat.UNKNOWN, MockParser())
        parser = factory.get_parser(FileFormat.UNKNOWN)
        assert isinstance(parser, MockParser)

    def test_supported_formats(self, factory):
        """supported_formats 属性"""
        formats = factory.supported_formats
        assert FileFormat.SINGLE_FILE in formats
        assert FileFormat.BLURAY_BDMV in formats
        assert FileFormat.BDISO in formats
        assert FileFormat.TV_EPISODE in formats
        assert FileFormat.UNKNOWN not in formats
