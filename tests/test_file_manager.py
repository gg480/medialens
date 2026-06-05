"""文件操作引擎单元测试"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from medialens.filemgr.file_manager import DirectoryOrganizer, FileManager
from medialens.models import (
    MatchResult,
    MatchSource,
    MediaType,
    ParsedMedia,
    RenameConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fm() -> FileManager:
    return FileManager()


@pytest.fixture
def org() -> DirectoryOrganizer:
    return DirectoryOrganizer()


@pytest.fixture
def movie_result() -> MatchResult:
    return MatchResult(
        matched=True,
        media_type=MediaType.MOVIE,
        tmdb_id=12345,
        title="功夫",
        original_title="Kung Fu Hustle",
        year=2004,
        source=MatchSource.TMDB_EXACT,
        confidence=95.0,
    )


@pytest.fixture
def tv_result() -> MatchResult:
    return MatchResult(
        matched=True,
        media_type=MediaType.TV,
        tmdb_id=67890,
        title="权力的游戏",
        original_title="Game of Thrones",
        year=2011,
        season=1,
        episode=1,
        source=MatchSource.TMDB_EXACT,
        confidence=98.0,
    )


@pytest.fixture
def rename_config() -> RenameConfig:
    return RenameConfig(
        create_hardlink=True,
        keep_original=True,
        organize_by="year",
    )


@pytest.fixture
def temp_dir() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as td:
        yield td


# ---------------------------------------------------------------------------
# generate_target_path
# ---------------------------------------------------------------------------

class TestGenerateTargetPath:
    """generate_target_path 路径生成测试"""

    def test_generate_movie_target_path(
        self, fm: FileManager, movie_result: MatchResult
    ):
        """电影路径生成：{target_dir}/{title} ({year})/{title} ({year}) [{quality}].{ext}"""
        path = fm.generate_target_path(
            source="D:/movies/功夫.2004.BluRay.1080p.mkv",
            target_dir="D:/output",
            result=movie_result,
            config=RenameConfig(),
        )
        expected = os.path.join(
            "D:/output", "功夫 (2004)", "功夫 (2004) [BluRay.1080p].mkv"
        )
        assert path == os.path.normpath(expected)

    def test_generate_movie_target_path_no_year(
        self, fm: FileManager,
    ):
        """电影无年份时的路径"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=1,
            title="Test Movie",
            original_title="Test",
        )
        path = fm.generate_target_path(
            source="/movies/test.1080p.mkv",
            target_dir="/output",
            result=result,
            config=RenameConfig(),
        )
        # 无年份时只显示 title
        assert "Test Movie" in path
        assert "mkv" in path

    def test_generate_tv_target_path(
        self, fm: FileManager, tv_result: MatchResult
    ):
        """电视剧路径生成：{target_dir}/{title}/Season {s:02d}/{title} - S{s:02d}E{e:02d} [{quality}].{ext}"""
        path = fm.generate_target_path(
            source="D:/tv/权力的游戏.S01E01.BluRay.1080p.mkv",
            target_dir="D:/output",
            result=tv_result,
            config=RenameConfig(),
        )
        expected = os.path.join(
            "D:/output", "权力的游戏", "Season 01",
            "权力的游戏 - S01E01 - [BluRay.1080p].mkv"
        )
        assert path == os.path.normpath(expected)

    def test_generate_tv_target_path_default_season_episode(
        self, fm: FileManager,
    ):
        """电视剧无季集信息时默认 S01E01"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.TV,
            tmdb_id=2,
            title="Test TV",
            original_title="Test",
        )
        path = fm.generate_target_path(
            source="/tv/test.1080p.mkv",
            target_dir="/output",
            result=result,
            config=RenameConfig(),
        )
        assert "Season 01" in path
        assert "S01E01" in path

    def test_generate_target_path_unknown_type(
        self, fm: FileManager,
    ):
        """未知媒体类型时直接平铺在 target_dir 下"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=3,
            title="SomeFile",
            original_title="SomeFile",
        )
        path = fm.generate_target_path(
            source="/test/some.1080p.mkv",
            target_dir="/output",
            result=result,
            config=RenameConfig(),
        )
        # 应包含 title + 扩展名
        assert "SomeFile" in path
        assert ".mkv" in path

    def test_generate_target_path_quality_from_source(
        self, fm: FileManager, movie_result: MatchResult
    ):
        """质量标签从源文件名自动提取"""
        path = fm.generate_target_path(
            source="/movies/功夫.2004.WEB-DL.2160p.mkv",
            target_dir="/output",
            result=movie_result,
            config=RenameConfig(),
        )
        assert "[WEB-DL.2160p]" in path

    def test_generate_target_path_quality_unknown(
        self, fm: FileManager, movie_result: MatchResult
    ):
        """无法提取质量标签时使用 Unknown"""
        path = fm.generate_target_path(
            source="/movies/功夫.mkv",
            target_dir="/output",
            result=movie_result,
            config=RenameConfig(),
        )
        assert "[Unknown]" in path


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------

class TestSafeFilename:
    """safe_filename 文件名清理测试"""

    def test_removes_invalid_chars(self, fm: FileManager):
        """清除 Windows 非法字符 \\ / : * ? \" < > |"""
        dirty = 'A:B*C?D"E<F>G|H\\I/J'
        clean = fm.safe_filename(dirty)
        assert clean == "ABCDEFGHIJ"

    def test_strips_whitespace(self, fm: FileManager):
        """清理后去除首尾空格"""
        assert fm.safe_filename("  hello  ") == "hello"

    def test_empty_fallback(self, fm: FileManager):
        """空字符串回退为 'untitled'"""
        assert fm.safe_filename("") == "untitled"
        assert fm.safe_filename("   ") == "untitled"

    def test_keeps_valid_chars(self, fm: FileManager):
        """保留合法字符"""
        name = "功夫.2004.1080p"
        assert fm.safe_filename(name) == name


# ---------------------------------------------------------------------------
# get_quality_tag
# ---------------------------------------------------------------------------

class TestQualityTag:
    """get_quality_tag 质量标签测试"""

    def test_both_present(self, fm: FileManager):
        """source + resolution 都有"""
        parsed = ParsedMedia(
            raw_path=Path("/test.mkv"),
            raw_filename="test.mkv",
            file_format="single_file",
            source="BluRay",
            resolution="1080p",
        )
        assert fm.get_quality_tag(parsed) == "BluRay.1080p"

    def test_only_source(self, fm: FileManager):
        """只有 source"""
        parsed = ParsedMedia(
            raw_path=Path("/test.mkv"),
            raw_filename="test.mkv",
            file_format="single_file",
            source="WEB-DL",
        )
        assert fm.get_quality_tag(parsed) == "WEB-DL"

    def test_only_resolution(self, fm: FileManager):
        """只有 resolution"""
        parsed = ParsedMedia(
            raw_path=Path("/test.mkv"),
            raw_filename="test.mkv",
            file_format="single_file",
            resolution="2160p",
        )
        assert fm.get_quality_tag(parsed) == "2160p"

    def test_neither(self, fm: FileManager):
        """都没有时返回 Unknown"""
        parsed = ParsedMedia(
            raw_path=Path("/test.mkv"),
            raw_filename="test.mkv",
            file_format="single_file",
        )
        assert fm.get_quality_tag(parsed) == "Unknown"


# ---------------------------------------------------------------------------
# ensure_dir
# ---------------------------------------------------------------------------

class TestEnsureDir:
    """ensure_dir 目录创建测试"""

    def test_creates_directory(self, fm: FileManager, temp_dir: str):
        """创建不存在的目录"""
        new_dir = Path(temp_dir) / "a" / "b" / "c"
        assert not new_dir.exists()
        assert fm.ensure_dir(str(new_dir)) is True
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_existing_directory(self, fm: FileManager, temp_dir: str):
        """已存在的目录返回 True"""
        assert fm.ensure_dir(temp_dir) is True

    def test_nested_creation(self, fm: FileManager, temp_dir: str):
        """递归创建多级目录"""
        deep = Path(temp_dir) / "x" / "y" / "z"
        assert fm.ensure_dir(str(deep)) is True
        assert deep.exists()


# ---------------------------------------------------------------------------
# dry_run_operation
# ---------------------------------------------------------------------------

class TestDryRun:
    """dry_run 预览测试"""

    def test_existing_source_same_drive(self, fm: FileManager, temp_dir: str):
        """源文件存在且同盘符时返回 True"""
        src = Path(temp_dir) / "source.mkv"
        src.write_text("test content")
        dst = Path(temp_dir) / "target.mkv"

        # 系统盘都是 C:，所以是同盘
        result = fm.dry_run_operation(str(src), str(dst))
        assert result is True

    def test_nonexistent_source(self, fm: FileManager):
        """源文件不存在时返回 False"""
        assert fm.dry_run_operation("/nonexistent/file.mkv", "/out/target.mkv") is False


# ---------------------------------------------------------------------------
# create_hardlink / copy_file / move_file（实际文件操作）
# ---------------------------------------------------------------------------

class TestFileOperations:
    """实际文件操作测试"""

    def test_copy_file(self, fm: FileManager, temp_dir: str):
        """copy2 保留内容"""
        src = Path(temp_dir) / "src.txt"
        dst = Path(temp_dir) / "dst.txt"
        src.write_text("hello")

        assert fm.copy_file(str(src), str(dst)) is True
        assert dst.read_text() == "hello"

    def test_copy_file_nonexistent(self, fm: FileManager, temp_dir: str):
        """源不存在时 copy 失败"""
        assert fm.copy_file("/nonexistent", str(Path(temp_dir) / "out")) is False

    def test_move_file(self, fm: FileManager, temp_dir: str):
        """move 后源文件消失"""
        src = Path(temp_dir) / "src.txt"
        dst = Path(temp_dir) / "dst.txt"
        src.write_text("move me")

        assert fm.move_file(str(src), str(dst)) is True
        assert not src.exists()
        assert dst.read_text() == "move me"

    def test_create_hardlink(self, fm: FileManager, temp_dir: str):
        """硬链接共享 inode"""
        src = Path(temp_dir) / "src.txt"
        dst = Path(temp_dir) / "dst.txt"
        src.write_text("hardlink test")

        assert fm.create_hardlink(str(src), str(dst)) is True
        assert dst.exists()
        assert dst.read_text() == "hardlink test"
        # 同一文件系统下，硬链接的 stat.st_ino 相同
        assert src.stat().st_ino == dst.stat().st_ino

    def test_create_hardlink_existing_dst(self, fm: FileManager, temp_dir: str):
        """目标已存在时硬链接失败"""
        src = Path(temp_dir) / "src.txt"
        dst = Path(temp_dir) / "dst.txt"
        src.write_text("src")
        dst.write_text("dst")
        assert fm.create_hardlink(str(src), str(dst)) is False


# ---------------------------------------------------------------------------
# organize_file（综合流程）
# ---------------------------------------------------------------------------

class TestOrganizeFile:
    """organize_file 完整流程测试"""

    def test_organize_file_dry_run(self, fm: FileManager, temp_dir: str, movie_result: MatchResult):
        """dry_run 模式不创建文件"""
        src = Path(temp_dir) / "功夫.2004.BluRay.1080p.mkv"
        src.write_text("movie content")

        op = fm.organize_file(
            source=str(src),
            target_dir=str(temp_dir),
            result=movie_result,
            config=RenameConfig(),
            dry_run=True,
        )
        assert op.dry_run is True
        assert op.operation == "hardlink"
        # dry_run 模式不实际创建
        assert not Path(op.target_path).exists()

    def test_organize_file_nonexistent_source(self, fm: FileManager, temp_dir: str, movie_result: MatchResult):
        """源文件不存在时返回错误 FileOperation"""
        op = fm.organize_file(
            source=str(Path(temp_dir) / "nonexistent.mkv"),
            target_dir=str(temp_dir),
            result=movie_result,
            config=RenameConfig(),
            dry_run=False,
        )
        assert op.success is False
        assert "不存在" in (op.error or "")

    def test_organize_file_copy(self, fm: FileManager, temp_dir: str, movie_result: MatchResult):
        """copy 模式创建新文件"""
        src = Path(temp_dir) / "功夫.2004.mkv"
        src.write_text("movie content")

        config = RenameConfig(create_hardlink=False, keep_original=True)
        op = fm.organize_file(
            source=str(src),
            target_dir=str(temp_dir),
            result=movie_result,
            config=config,
            dry_run=False,
        )
        assert op.success is True
        assert op.operation == "copy"
        assert Path(op.target_path).exists()
        assert src.exists()  # copy 保留源文件

    def test_organize_file_move(self, fm: FileManager, temp_dir: str, movie_result: MatchResult):
        """move 模式源文件消失"""
        src = Path(temp_dir) / "功夫.2004.mkv"
        src.write_text("movie content")

        config = RenameConfig(create_hardlink=False, keep_original=False)
        op = fm.organize_file(
            source=str(src),
            target_dir=str(temp_dir),
            result=movie_result,
            config=config,
            dry_run=False,
        )
        assert op.success is True
        assert op.operation == "move"
        assert Path(op.target_path).exists()
        assert not src.exists()  # move 后源文件消失


# ---------------------------------------------------------------------------
# get_file_size
# ---------------------------------------------------------------------------

class TestFileSize:
    """get_file_size 文件大小测试"""

    def test_get_file_size(self, fm: FileManager, temp_dir: str):
        p = Path(temp_dir) / "test.bin"
        p.write_bytes(b"\x00" * 1024)
        assert fm.get_file_size(str(p)) == 1024

    def test_get_file_size_nonexistent(self, fm: FileManager):
        assert fm.get_file_size("/nonexistent") == 0


# ---------------------------------------------------------------------------
# DirectoryOrganizer
# ---------------------------------------------------------------------------

class TestDirectoryOrganizer:
    """DirectoryOrganizer 目录策略测试"""

    def test_organize_movie_folder_by_year(self, org: DirectoryOrganizer):
        """year 策略: {target}/{year}/{title} ({year})"""
        path = org.organize_movie_folder("功夫", 2004, "/movies", strategy="year")
        expected = os.path.join("/movies", "2004", "功夫 (2004)")
        assert path == os.path.normpath(expected)

    def test_organize_movie_folder_by_year_no_year(self, org: DirectoryOrganizer):
        """year 策略无年份时归入 Unknown"""
        path = org.organize_movie_folder("Test", 0, "/movies", strategy="year")
        assert "Unknown" in path

    def test_organize_movie_folder_by_first_letter(self, org: DirectoryOrganizer):
        """first_letter 策略: {target}/{首字母}/{title} ({year})"""
        path = org.organize_movie_folder("Avatar", 2009, "/movies", strategy="first_letter")
        expected_prefix = os.path.normpath("/movies/A/")
        assert path.startswith(expected_prefix)

    def test_organize_movie_folder_by_first_letter_special(self, org: DirectoryOrganizer):
        """first_letter 策略非字母开头归入 #"""
        path = org.organize_movie_folder("123 Movie", 2020, "/movies", strategy="first_letter")
        expected_prefix = os.path.normpath("/movies/#/")
        assert path.startswith(expected_prefix)

    def test_organize_movie_folder_flat(self, org: DirectoryOrganizer):
        """flat 策略: {target}/{title} ({year})"""
        path = org.organize_movie_folder("Inception", 2010, "/movies", strategy="flat")
        expected = os.path.join("/movies", "Inception (2010)")
        assert path == os.path.normpath(expected)

    def test_organize_tv_folder(self, org: DirectoryOrganizer):
        """电视剧目录: {target}/{title}"""
        path = org.organize_tv_folder("绝命毒师", "/tv")
        expected = os.path.join("/tv", "绝命毒师")
        assert path == os.path.normpath(expected)

    def test_organize_tv_folder_safe(self, org: DirectoryOrganizer):
        """电视剧目录清理非法字符"""
        path = org.organize_tv_folder("Bad:Title?", "/tv")
        expected = os.path.join("/tv", "BadTitle")
        assert path == os.path.normpath(expected)


# ---------------------------------------------------------------------------
# organize_directory（批量整理）
# ---------------------------------------------------------------------------

class TestOrganizeDirectory:
    """批量目录整理测试"""

    def test_organize_directory(self, fm: FileManager, org: DirectoryOrganizer, temp_dir: str):
        """批量整理多个文件"""
        # 创建测试文件
        files = []
        for name in ["movie1.2004.mkv", "movie2.2010.mkv"]:
            p = Path(temp_dir) / name
            p.write_text("content")
            files.append(str(p))

        results = fm.organize_directory(files, str(temp_dir), org)
        assert len(results) == 2
        for op in results:
            assert op.success is True

    def test_organize_directory_nonexistent(self, fm: FileManager, org: DirectoryOrganizer, temp_dir: str):
        """不存在的文件返回错误"""
        results = fm.organize_directory(
            ["/nonexistent/file.mkv"], str(temp_dir), org
        )
        assert len(results) == 1
        assert results[0].success is False
