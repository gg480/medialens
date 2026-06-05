"""
FormatDetector 模块单元测试

使用 pytest 的 tmp_path 临时目录模拟文件系统。
测试覆盖：
- detect_format: 5 种格式路径检测 + 边界情况
- is_bdmv_structure: BDMV 目录结构判定
- is_episode_filename: 多种剧集命名模式
- get_video_files: 递归视频文件收集
- is_bdmv_file: BDMV/STREAM/ 层级检测
- 错误处理：路径不存在、空目录、权限错误（mock）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from medialens.detector.format_detector import (
    VIDEO_EXTENSIONS,
    detect_format,
    get_video_files,
    is_bdmv_file,
    is_bdmv_structure,
    is_episode_filename,
)
from medialens.models import FileFormat


# ============================================================================
# detect_format 测试
# ============================================================================


class TestDetectFormat:
    """detect_format 核心检测逻辑"""

    def test_detect_single_mkv(self, tmp_path: Path):
        """单文件 .mkv → SINGLE_FILE"""
        video = tmp_path / "Avengers.Endgame.2019.2160p.mkv"
        video.write_text("dummy content")
        assert detect_format(str(video)) == FileFormat.SINGLE_FILE

    def test_detect_single_mp4(self, tmp_path: Path):
        """单文件 .mp4 → SINGLE_FILE"""
        video = tmp_path / "Inception.2010.1080p.mp4"
        video.write_text("dummy content")
        assert detect_format(str(video)) == FileFormat.SINGLE_FILE

    def test_detect_bdmv_directory(self, tmp_path: Path):
        """BDMV 目录结构 → BLURAY_BDMV"""
        bdmv = tmp_path / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        # 放入一个 m2ts 文件使目录看起来更真实
        (bdmv / "STREAM" / "00001.m2ts").write_text("dummy")
        assert detect_format(str(tmp_path)) == FileFormat.BLURAY_BDMV

    def test_detect_bdmv_with_movie_name(self, tmp_path: Path):
        """命名如 '功夫 (2004) BluRay REMUX/' 的目录，内含 BDMV → BLURAY_BDMV"""
        movie_dir = tmp_path / "功夫 (2004) BluRay REMUX"
        movie_dir.mkdir()
        bdmv = movie_dir / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        assert detect_format(str(movie_dir)) == FileFormat.BLURAY_BDMV

    def test_detect_iso_file(self, tmp_path: Path):
        """.iso 文件 → BDISO"""
        iso = tmp_path / "The.Matrix.1999.iso"
        iso.write_text("dummy")
        assert detect_format(str(iso)) == FileFormat.BDISO

    def test_detect_bdiso_file(self, tmp_path: Path):
        """.bdiso 文件 → BDISO"""
        bdiso = tmp_path / "Interstellar.2014.bdiso"
        bdiso.write_text("dummy")
        assert detect_format(str(bdiso)) == FileFormat.BDISO

    def test_detect_tv_episode_sxxexx(self, tmp_path: Path):
        """文件名含 SxxExx → TV_EPISODE"""
        video = tmp_path / "Breaking.Bad.S01E01.1080p.mkv"
        video.write_text("dummy")
        assert detect_format(str(video)) == FileFormat.TV_EPISODE

    def test_detect_tv_episode_chinese(self, tmp_path: Path):
        """文件名含'第x集' → TV_EPISODE"""
        video = tmp_path / "权力的游戏 第03集.mkv"
        video.write_text("dummy")
        assert detect_format(str(video)) == FileFormat.TV_EPISODE

    def test_detect_tv_season_directory(self, tmp_path: Path):
        """多文件剧集目录 → TV_SEASON"""
        season_dir = tmp_path / "Breaking.Bad.S01"
        season_dir.mkdir()
        for ep in range(1, 4):
            (season_dir / f"Breaking.Bad.S01E{ep:02d}.1080p.mkv").write_text(
                "dummy"
            )
        assert detect_format(str(season_dir)) == FileFormat.TV_SEASON

    def test_detect_unknown_file(self, tmp_path: Path):
        """不支持的文件 → UNKNOWN"""
        txt = tmp_path / "readme.txt"
        txt.write_text("hello")
        assert detect_format(str(txt)) == FileFormat.UNKNOWN

    def test_detect_unknown_extension(self, tmp_path: Path):
        """视频扩展名不支持 → UNKNOWN"""
        weird = tmp_path / "video.aaa"
        weird.write_text("dummy")
        assert detect_format(str(weird)) == FileFormat.UNKNOWN

    def test_detect_empty_directory(self, tmp_path: Path):
        """空目录 → UNKNOWN"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assert detect_format(str(empty_dir)) == FileFormat.UNKNOWN

    def test_detect_nonexistent_path(self):
        """路径不存在 → UNKNOWN"""
        assert detect_format(r"C:\nonexistent_path_12345") == FileFormat.UNKNOWN

    def test_detect_directory_with_one_video(self, tmp_path: Path):
        """目录下只有单个视频文件 → UNKNOWN（非 TV_SEASON 条件）"""
        d = tmp_path / "random_dir"
        d.mkdir()
        (d / "movie.mkv").write_text("dummy")
        assert detect_format(str(d)) == FileFormat.UNKNOWN

    def test_detect_bdmv_inside_movie_name_dir(self, tmp_path: Path):
        """带标题名的 BDMV 父目录 → BLURAY_BDMV"""
        movie_dir = tmp_path / "The Dark Knight (2008)"
        movie_dir.mkdir()
        bdmv = movie_dir / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        assert detect_format(str(movie_dir)) == FileFormat.BLURAY_BDMV


# ============================================================================
# is_bdmv_structure 测试
# ============================================================================


class TestIsBdmvStructure:
    """BDMV 目录结构判定"""

    def test_valid_bdmv(self, tmp_path: Path):
        """完整 BDMV 结构 → True"""
        bdmv = tmp_path / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        assert is_bdmv_structure(str(tmp_path)) is True

    def test_missing_playlist(self, tmp_path: Path):
        """缺少 PLAYLIST → False"""
        bdmv = tmp_path / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        assert is_bdmv_structure(str(tmp_path)) is False

    def test_missing_stream(self, tmp_path: Path):
        """缺少 STREAM → False"""
        bdmv = tmp_path / "BDMV"
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        assert is_bdmv_structure(str(tmp_path)) is False

    def test_no_bdmv_dir(self, tmp_path: Path):
        """没有 BDMV 目录 → False"""
        assert is_bdmv_structure(str(tmp_path)) is False

    def test_bdmv_on_file(self, tmp_path: Path):
        """传入文件路径 → False"""
        f = tmp_path / "somefile.txt"
        f.write_text("hello")
        assert is_bdmv_structure(str(f)) is False

    def test_nonexistent_path(self):
        """不存在的路径 → False"""
        assert is_bdmv_structure(r"C:\nonexistent_bdmv") is False

    def test_deep_nested_bdmv(self, tmp_path: Path):
        """深层嵌套目录中放置 BDMV → True"""
        deep = tmp_path / "movies" / "Inception (2010)" / "Inception 2010 BluRay REMUX"
        deep.mkdir(parents=True)
        bdmv = deep / "BDMV"
        (bdmv / "STREAM").mkdir(parents=True)
        (bdmv / "PLAYLIST").mkdir(parents=True)
        (bdmv / "CLIPINF").mkdir(parents=True)
        assert is_bdmv_structure(str(deep)) is True


# ============================================================================
# is_episode_filename 测试
# ============================================================================


class TestIsEpisodeFilename:
    """剧集文件名模式匹配"""

    @pytest.mark.parametrize(
        "filename",
        [
            "Breaking.Bad.S01E01.1080p.mkv",
            "Game.of.Thrones.s01e01.2160p.mkv",
            "Friends.S1E1.DVDRip.avi",
            "Dexter.S08E12.HDTV.x264.mkv",
            "The.Office.US.S09E23.1080p.mkv",
            "Lost.S06E17E18.1080p.mkv",       # 多集连播
            "Westworld.S03E01-E02.2160p.mkv",  # 连播带连接符
            "Stranger.Things.S01E01.2160p.mkv",
            "Rick.and.Morty.s4e5.mkv",
        ],
    )
    def test_sxxexx_patterns(self, filename: str):
        """SxxExx 系列模式 → True（含大小写、多集连播）"""
        assert is_episode_filename(filename) is True

    @pytest.mark.parametrize(
        "filename",
        [
            "Game.of.Thrones.E01.1080p.mkv",
            "Breaking.Bad.e01.1080p.mkv",
            "Band.of.Brothers.E10.1080p.mkv",
        ],
    )
    def test_exx_patterns(self, filename: str):
        """Exx 无季号模式 → True"""
        assert is_episode_filename(filename) is True

    @pytest.mark.parametrize(
        "filename",
        [
            "权力的游戏 第03集.mkv",
            "琅琊榜 第01集.1080p.mkv",
            "武林外传 第1集.mkv",
            "西游记 第10话.mp4",
        ],
    )
    def test_chinese_patterns(self, filename: str):
        """第x集/话 中文模式 → True"""
        assert is_episode_filename(filename) is True

    @pytest.mark.parametrize(
        "filename",
        [
            "Season 01",
            "Season 02",
            "Season 1",
            "season 01",
        ],
    )
    def test_season_only_patterns(self, filename: str):
        """Season 模式（无集号）→ True"""
        assert is_episode_filename(filename) is True

    @pytest.mark.parametrize(
        "filename",
        [
            "Avengers.Endgame.2019.2160p.mkv",
            "The.Matrix.1999.1080p.mkv",
            "Inception.2010.mkv",
            "E.T.the.Extra.Terrestrial.mkv",           # E.T. 不应匹配 E01
            "readme.txt",
            "星际穿越.mkv",
            "陈情令.mp4",                                # 不含剧集标记
            "The.Walking.Dead.1080p.mkv",               # 无 S/E 标记
        ],
    )
    def test_non_episode(self, filename: str):
        """非剧集文件 → False"""
        assert is_episode_filename(filename) is False

    def test_empty_string(self):
        """空字符串 → False"""
        assert is_episode_filename("") is False

    def test_filename_without_ext(self):
        """无扩展名但含剧集标记 → True"""
        assert is_episode_filename("S01E01") is True


# ============================================================================
# get_video_files 测试
# ============================================================================


class TestGetVideoFiles:
    """递归视频文件收集"""

    def test_single_video(self, tmp_path: Path):
        """目录下单个视频文件"""
        (tmp_path / "movie.mkv").write_text("dummy")
        files = get_video_files(str(tmp_path))
        assert len(files) == 1
        assert files[0].endswith("movie.mkv")

    def test_multiple_videos(self, tmp_path: Path):
        """目录下多个视频文件"""
        for name in ["a.mkv", "b.mp4", "c.avi"]:
            (tmp_path / name).write_text("dummy")
        files = get_video_files(str(tmp_path))
        assert len(files) == 3

    def test_recursive_subdirs(self, tmp_path: Path):
        """子目录中的视频文件也被递归收集"""
        sub = tmp_path / "subfolder"
        sub.mkdir()
        (sub / "deep.mkv").write_text("dummy")
        (tmp_path / "top.mp4").write_text("dummy")
        files = get_video_files(str(tmp_path))
        assert len(files) == 2

    def test_ignore_non_video(self, tmp_path: Path):
        """非视频文件被忽略"""
        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "movie.mkv").write_text("dummy")
        (tmp_path / "poster.jpg").write_text("dummy")
        files = get_video_files(str(tmp_path))
        assert len(files) == 1
        assert all(f.endswith(".mkv") for f in files)

    def test_all_video_extensions(self, tmp_path: Path):
        """所有 VIDEO_EXTENSIONS 扩展名都被识别"""
        for ext in VIDEO_EXTENSIONS:
            (tmp_path / f"video{ext}").write_text("dummy")
        files = get_video_files(str(tmp_path))
        assert len(files) == len(VIDEO_EXTENSIONS)

    def test_empty_directory(self, tmp_path: Path):
        """空目录返回空列表"""
        assert get_video_files(str(tmp_path)) == []

    def test_nonexistent_path(self):
        """不存在的路径返回空列表"""
        assert get_video_files(r"C:\nonexistent_path_xyz") == []


# ============================================================================
# is_bdmv_file 测试
# ============================================================================


class TestIsBdmvFile:
    """BDMV/STREAM/ 下 m2ts 文件判定"""

    def test_bdmv_stream_file(self, tmp_path: Path):
        """BDMV/STREAM/ 下文件 → True"""
        bdmv_dir = tmp_path / "BDMV" / "STREAM"
        bdmv_dir.mkdir(parents=True)
        m2ts = bdmv_dir / "00001.m2ts"
        m2ts.write_text("dummy")
        assert is_bdmv_file(str(m2ts)) is True

    def test_non_bdmv_file(self, tmp_path: Path):
        """普通 m2ts → False"""
        f = tmp_path / "video.m2ts"
        f.write_text("dummy")
        assert is_bdmv_file(str(f)) is False

    def test_non_existent_path(self):
        """不存在的路径 → False"""
        assert is_bdmv_file(r"C:\nonexistent.m2ts") is False

    def test_case_insensitive_bdmv(self, tmp_path: Path):
        """路径大小写不敏感 → True"""
        bdmv_dir = tmp_path / "bdmv" / "stream"
        bdmv_dir.mkdir(parents=True)
        m2ts = bdmv_dir / "00001.m2ts"
        m2ts.write_text("dummy")
        assert is_bdmv_file(str(m2ts)) is True


# ============================================================================
# VIDEO_EXTENSIONS 常量测试
# ============================================================================


class TestVideoExtensions:
    """VIDEO_EXTENSIONS 常量的完整性和合理性"""

    def test_contains_common_extensions(self):
        """包含常见的视频扩展名"""
        common = {".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".iso", ".mov"}
        assert common.issubset(VIDEO_EXTENSIONS)

    def test_all_lowercase(self):
        """所有扩展名为小写"""
        for ext in VIDEO_EXTENSIONS:
            assert ext == ext.lower(), f"{ext} 不是小写"

    def test_all_start_with_dot(self):
        """所有扩展名以点开头"""
        for ext in VIDEO_EXTENSIONS:
            assert ext.startswith("."), f"{ext} 不以点开头"
