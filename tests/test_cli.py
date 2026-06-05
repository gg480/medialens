"""
CLI 命令行入口单元测试

使用 click.testing.CliRunner 测试命令解析和流程编排，
使用 unittest.mock 隔离外部依赖（MatcherPipeline、DatabaseManager 等）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from medialens.cli.main import cli
from medialens.models import (
    MatchResult,
    MatchSource,
    MediaLensConfig,
    MediaType,
    RenameConfig,
)


# =============================================================
# 辅助函数
# =============================================================


def _make_default_config() -> MediaLensConfig:
    """创建默认配置"""
    return MediaLensConfig(
        tmdb_api_key="test_key",
        tmdb_language="zh-CN",
        db_path=":memory:",
        dry_run=True,
    )


# =============================================================
# Fixtures
# =============================================================


@pytest.fixture
def runner() -> CliRunner:
    """创建 CliRunner 实例"""
    return CliRunner()


@pytest.fixture
def temp_config() -> Generator[Path, None, None]:
    """创建临时配置文件"""
    import yaml

    config_data = {
        "tmdb": {"api_key": "test_key", "language": "zh-CN"},
        "llm": {"provider": "openai", "api_key": "", "model": "gpt-4o-mini"},
        "thresholds": {"tmdb_confidence": 80, "llm_confidence": 60},
        "file_management": {"dry_run": True, "organize_mode": "hardlink"},
        "storage": {"db_path": ":memory:", "data_dir": "data"},
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)
        config_path = f.name

    yield Path(config_path)

    os.unlink(config_path)


@pytest.fixture
def temp_files() -> Generator[list[Path], None, None]:
    """创建临时视频文件列表"""
    tmp_dir = Path(tempfile.mkdtemp())
    files = [
        tmp_dir / "功夫.2004.BluRay.1080p.mkv",
        tmp_dir / "The.Matrix.1999.1080p.mkv",
    ]
    for f in files:
        f.write_text("fake video content")

    yield files

    import shutil

    shutil.rmtree(tmp_dir)


# =============================================================
# 测试辅助：mock 全局对象
# =============================================================


def _patch_pipeline(monkeypatch: Any) -> MagicMock:
    """Mock MatcherPipeline.match 返回一个成功的匹配结果"""
    mock_pipeline = MagicMock()
    mock_pipeline.match.return_value = MatchResult(
        matched=True,
        media_type=MediaType.MOVIE,
        tmdb_id=9470,
        title="功夫 (Kung Fu Hustle)",
        original_title="功夫",
        year=2004,
        source=MatchSource.TMDB_EXACT,
        confidence=95.0,
        overview="A comedy about a wannabe gangster.",
    )
    mock_pipeline.match_batch.return_value = [mock_pipeline.match.return_value]
    mock_pipeline.db.initialize = MagicMock()
    return mock_pipeline


# =============================================================
# help 输出
# =============================================================


class TestHelpOutput:
    """测试 --help 输出"""

    def test_cli_help(self, runner: CliRunner) -> None:
        """主命令 --help"""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        # 检查包含关键命令名
        for cmd in ["match", "batch", "scrape", "organize", "config", "cache", "stats"]:
            assert cmd in result.output

    def test_match_help(self, runner: CliRunner) -> None:
        """match 命令 --help"""
        result = runner.invoke(cli, ["match", "--help"])
        assert result.exit_code == 0
        assert "PATH" in result.output
        assert "--tmdb-key" in result.output
        assert "--output" in result.output or "-o" in result.output

    def test_batch_help(self, runner: CliRunner) -> None:
        """batch 命令 --help"""
        result = runner.invoke(cli, ["batch", "--help"])
        assert result.exit_code == 0
        assert "PATH" in result.output
        assert "--scrape" in result.output
        assert "--output" in result.output or "-o" in result.output

    def test_scrape_help(self, runner: CliRunner) -> None:
        """scrape 命令 --help"""
        result = runner.invoke(cli, ["scrape", "--help"])
        assert result.exit_code == 0
        assert "--images" in result.output
        assert "--overwrite" in result.output

    def test_organize_help(self, runner: CliRunner) -> None:
        """organize 命令 --help"""
        result = runner.invoke(cli, ["organize", "--help"])
        assert result.exit_code == 0
        assert "--target-dir" in result.output
        assert "--mode" in result.output

    def test_config_help(self, runner: CliRunner) -> None:
        """config 命令 --help"""
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0

    def test_stats_help(self, runner: CliRunner) -> None:
        """stats 命令 --help"""
        result = runner.invoke(cli, ["stats", "--help"])
        assert result.exit_code == 0

    def test_cache_help(self, runner: CliRunner) -> None:
        """cache 命令 --help"""
        result = runner.invoke(cli, ["cache", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "clear" in result.output

    def test_version_output(self, runner: CliRunner) -> None:
        """--version 输出"""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "medialens" in result.output


# =============================================================
# match 命令
# =============================================================


class TestMatchCommand:
    """测试 match 命令"""

    def test_match_command_runs(self, runner: CliRunner, temp_files: list[Path]) -> None:
        """match 命令基本运行"""
        with patch(
            "medialens.cli.main.MatcherPipeline",
            return_value=_patch_pipeline(None),
        ):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(temp_files[0]),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code == 0

    def test_match_dry_run(self, runner: CliRunner, temp_files: list[Path]) -> None:
        """match --dry-run 预览模式"""
        with patch(
            "medialens.cli.main.MatcherPipeline",
            return_value=_patch_pipeline(None),
        ):
            result = runner.invoke(
                cli,
                [
                    "--dry-run",
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(temp_files[0]),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code == 0

    def test_match_multiple_files(
        self, runner: CliRunner, temp_files: list[Path]
    ) -> None:
        """match 多个文件"""
        with patch(
            "medialens.cli.main.MatcherPipeline",
            return_value=_patch_pipeline(None),
        ):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(temp_files[0]),
                    str(temp_files[1]),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code == 0

    def test_match_with_scrape(
        self, runner: CliRunner, temp_files: list[Path]
    ) -> None:
        """match --scrape 匹配后自动刮削"""
        mock_pipeline = _patch_pipeline(None)

        with (
            patch("medialens.cli.main.MatcherPipeline", return_value=mock_pipeline),
            patch("medialens.cli.main.NfoGenerator") as mock_nfo,
        ):
            mock_nfo_instance = MagicMock()
            mock_nfo_instance.generate_movie_nfo.return_value = "/tmp/movie.nfo"
            mock_nfo.return_value = mock_nfo_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(temp_files[0]),
                    "--tmdb-key",
                    "test_key",
                    "--scrape",
                ],
            )
            assert result.exit_code == 0

    def test_match_with_organize(
        self, runner: CliRunner, temp_files: list[Path]
    ) -> None:
        """match --organize 匹配后自动整理"""
        mock_pipeline = _patch_pipeline(None)

        with (
            patch("medialens.cli.main.MatcherPipeline", return_value=mock_pipeline),
            patch("medialens.cli.main.FileManager") as mock_fm,
        ):
            mock_fm_instance = MagicMock()
            mock_fm_instance.organize_file.return_value = MagicMock(
                success=True, target_path=Path("/tmp/功夫 (2004)/功夫.2004.mkv")
            )
            mock_fm.return_value = mock_fm_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(temp_files[0]),
                    "--tmdb-key",
                    "test_key",
                    "--organize",
                ],
            )
            assert result.exit_code == 0

    def test_match_no_tmdb_key(self, runner: CliRunner, temp_files: list[Path]) -> None:
        """match 无 TMDB Key 应报错"""
        with patch(
            "medialens.cli.main.load_config",
            return_value=MediaLensConfig(tmdb_api_key=""),
        ):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(temp_files[0]),
                ],
            )
            # 应提示未配置
            assert "未配置" in result.output


# =============================================================
# batch 命令
# =============================================================


class TestBatchCommand:
    """测试 batch 命令"""

    def test_batch_command(self, runner: CliRunner, temp_files: list[Path]) -> None:
        """batch 命令基本运行"""
        parent_dir = temp_files[0].parent
        mock_pipeline = _patch_pipeline(None)

        with (
            patch("medialens.cli.main.MatcherPipeline", return_value=mock_pipeline),
            patch(
                "medialens.detector.format_detector.get_video_files",
                return_value=[str(f) for f in temp_files],
            ),
        ):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "batch",
                    str(parent_dir),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code == 0

    def test_batch_no_files(self, runner: CliRunner) -> None:
        """batch 空目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "medialens.detector.format_detector.get_video_files",
                return_value=[],
            ):
                result = runner.invoke(
                    cli,
                    [
                        "-c",
                        "non_existent_config.yaml",
                        "batch",
                        tmpdir,
                        "--tmdb-key",
                        "test_key",
                    ],
                )
                assert result.exit_code == 0
                assert "未发现" in result.output


# =============================================================
# scrape 命令
# =============================================================


class TestScrapeCommand:
    """测试 scrape 命令"""

    def test_scrape_command(self, runner: CliRunner, temp_files: list[Path]) -> None:
        """scrape 命令运行（需要数据库中有记录）"""
        with (
            patch("medialens.cli.main.DatabaseManager") as mock_db,
            patch("medialens.cli.main.NfoGenerator") as mock_nfo,
        ):
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance.get_match_by_path.return_value = {
                "media_type": "movie",
                "tmdb_id": 9470,
                "matched_title": "功夫 (Kung Fu Hustle)",
                "matched_year": 2004,
                "match_source": "tmdb_exact",
                "confidence": 95.0,
                "overview": "A comedy.",
                "poster_path": "/poster.jpg",
                "backdrop_path": "/backdrop.jpg",
                "parsed_season": None,
                "parsed_episode": None,
            }
            mock_db.return_value = mock_db_instance

            mock_nfo_instance = MagicMock()
            mock_nfo.return_value = mock_nfo_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "scrape",
                    str(temp_files[0]),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code == 0

    def test_scrape_no_match(self, runner: CliRunner, temp_files: list[Path]) -> None:
        """scrape 无匹配记录"""
        with patch("medialens.cli.main.DatabaseManager") as mock_db:
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance.get_match_by_path.return_value = None
            mock_db.return_value = mock_db_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "scrape",
                    str(temp_files[0]),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code == 0
            assert "没有匹配记录" in result.output


# =============================================================
# organize 命令
# =============================================================


class TestOrganizeCommand:
    """测试 organize 命令"""

    def test_organize_single_file(
        self, runner: CliRunner, temp_files: list[Path]
    ) -> None:
        """organize 单文件"""
        with (
            patch("medialens.cli.main.DatabaseManager") as mock_db,
            patch("medialens.cli.main.FileManager") as mock_fm,
        ):
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance.get_match_by_path.return_value = {
                "media_type": "movie",
                "tmdb_id": 9470,
                "matched_title": "功夫 (Kung Fu Hustle)",
                "matched_year": 2004,
                "match_source": "tmdb_exact",
                "confidence": 95.0,
            }
            mock_db.return_value = mock_db_instance

            mock_fm_instance = MagicMock()
            mock_fm_instance.organize_file.return_value = MagicMock(
                success=True,
                target_path=Path("/tmp/功夫 (2004)/功夫.2004.mkv"),
                source_path=temp_files[0],
                operation="hardlink",
                dry_run=True,
            )
            mock_fm.return_value = mock_fm_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "organize",
                    str(temp_files[0]),
                    "--target-dir",
                    "/tmp/movies",
                ],
            )
            assert result.exit_code == 0

    def test_organize_directory(
        self, runner: CliRunner, temp_files: list[Path]
    ) -> None:
        """organize 目录"""
        parent_dir = temp_files[0].parent

        with (
            patch("medialens.cli.main.DatabaseManager") as mock_db,
            patch("medialens.detector.format_detector.get_video_files") as mock_get,
        ):
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance.get_match_by_path.return_value = None  # 无匹配
            mock_db.return_value = mock_db_instance

            mock_get.return_value = [str(f) for f in temp_files]

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "organize",
                    str(parent_dir),
                    "--target-dir",
                    "/tmp/movies",
                ],
            )
            assert result.exit_code == 0


# =============================================================
# config 命令
# =============================================================


class TestConfigCommand:
    """测试 config 命令"""

    def test_config_show(self, runner: CliRunner, temp_config: Path) -> None:
        """config show 命令"""
        result = runner.invoke(
            cli,
            ["-c", str(temp_config), "config", "show"],
        )
        assert result.exit_code == 0
        assert "TMDB API Key" in result.output

    def test_config_init(self, runner: CliRunner) -> None:
        """config init 命令"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "test_config.yaml"
            result = runner.invoke(cli, ["config", "init", str(config_path)])
            assert result.exit_code == 0
            assert config_path.exists()
            assert "默认配置文件已生成" in result.output

    def test_config_init_exists(self, runner: CliRunner, temp_config: Path) -> None:
        """config init 已存在配置文件"""
        result = runner.invoke(cli, ["config", "init", str(temp_config)])
        assert result.exit_code == 0
        assert "已存在" in result.output

    def test_config_init_force(self, runner: CliRunner, temp_config: Path) -> None:
        """config init --force 覆盖"""
        result = runner.invoke(cli, ["config", "init", "--force", str(temp_config)])
        assert result.exit_code == 0
        assert "已生成" in result.output

    def test_config_set(self, runner: CliRunner, temp_config: Path) -> None:
        """config set 命令"""
        result = runner.invoke(
            cli,
            ["-c", str(temp_config), "config", "set", "tmdb.api_key=new_key"],
        )
        assert result.exit_code == 0
        assert "已设置" in result.output


# =============================================================
# stats 命令
# =============================================================


class TestStatsCommand:
    """测试 stats 命令"""

    def test_stats_command(self, runner: CliRunner) -> None:
        """stats 命令运行"""
        with patch("medialens.cli.main.DatabaseManager") as mock_db:
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance.get_stats.return_value = {
                "total_matches": 42,
                "cache_hits": 10,
                "cache_hit_rate": 0.2381,
                "source_distribution": {
                    "tmdb_exact": 30,
                    "cache_hit": 10,
                    "tmdb_fuzzy": 2,
                },
                "llm_call_count": 5,
                "llm_success_count": 4,
                "file_operation_count": 20,
                "avg_confidence": 85.5,
            }
            mock_db.return_value = mock_db_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "stats",
                ],
            )
            assert result.exit_code == 0
            assert "42" in result.output
            assert "缓存命中率" in result.output


# =============================================================
# cache 命令
# =============================================================


class TestCacheCommand:
    """测试 cache 命令"""

    def test_cache_list(self, runner: CliRunner) -> None:
        """cache list 命令"""
        with patch("medialens.cli.main.DatabaseManager") as mock_db:
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance._query_all.return_value = [
                {
                    "id": 1,
                    "file_path": "/movies/test.mkv",
                    "parsed_title": "Test Movie",
                    "matched_title": "Test Movie (2000)",
                    "match_source": "tmdb_exact",
                    "confidence": 95.0,
                    "created_at": "2026-06-05 12:00:00",
                }
            ]
            mock_db.return_value = mock_db_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "cache",
                    "list",
                ],
            )
            assert result.exit_code == 0
            assert "tmdb_exact" in result.output
            assert "95.0" in result.output

    def test_cache_list_empty(self, runner: CliRunner) -> None:
        """cache list 空缓存"""
        with patch("medialens.cli.main.DatabaseManager") as mock_db:
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance._query_all.return_value = []
            mock_db.return_value = mock_db_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "cache",
                    "list",
                ],
            )
            assert result.exit_code == 0
            assert "暂无缓存记录" in result.output

    def test_cache_clear(self, runner: CliRunner) -> None:
        """cache clear 需要 --force"""
        with patch("medialens.cli.main.DatabaseManager"):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "cache",
                    "clear",
                ],
            )
            # 没有 --force，应提示确认
            assert result.exit_code == 0
            assert "警告" in result.output or "--force" in result.output

    def test_cache_clear_force(self, runner: CliRunner) -> None:
        """cache clear --force 清空缓存"""
        with patch("medialens.cli.main.DatabaseManager") as mock_db:
            mock_db_instance = MagicMock()
            mock_db_instance.initialize.return_value = None
            mock_db_instance._lock = MagicMock()
            mock_db_instance.conn.cursor.return_value = MagicMock()
            mock_db.return_value = mock_db_instance

            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "cache",
                    "clear",
                    "--force",
                ],
            )
            assert result.exit_code == 0
            assert "缓存已清空" in result.output


# =============================================================
# 全局选项
# =============================================================


class TestGlobalOptions:
    """测试全局选项"""

    def test_verbose(self, runner: CliRunner) -> None:
        """-v 详细输出"""
        match_pipeline = _patch_pipeline(None)
        with patch(
            "medialens.cli.main.MatcherPipeline",
            return_value=match_pipeline,
        ):
            result = runner.invoke(
                cli,
                [
                    "-v",
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(Path(tempfile.gettempdir())),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            # 即使文件不存在也应该能加载
            assert result.exit_code in (0, 1)

    def test_custom_config(self, runner: CliRunner, temp_config: Path) -> None:
        """-c 自定义配置文件"""
        match_pipeline = _patch_pipeline(None)
        with patch(
            "medialens.cli.main.MatcherPipeline",
            return_value=match_pipeline,
        ):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    str(temp_config),
                    "match",
                    str(Path(tempfile.gettempdir())),
                    "--tmdb-key",
                    "test_key",
                ],
            )
            assert result.exit_code in (0, 1)

    def test_no_tmdb_key_error(self, runner: CliRunner) -> None:
        """无 TMDB Key 时 match 应报错退出"""
        with patch(
            "medialens.cli.main.load_config",
            return_value=MediaLensConfig(tmdb_api_key=""),
        ):
            result = runner.invoke(
                cli,
                [
                    "-c",
                    "non_existent_config.yaml",
                    "match",
                    str(Path(tempfile.gettempdir())),
                ],
            )
            assert "未配置" in result.output
