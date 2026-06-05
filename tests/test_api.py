"""
MediaLens REST API 单元测试

使用 FastAPI TestClient 测试所有端点。
临时 SQLite 数据库通过 unittest.mock 注入。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from medialens.api.server import app
from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaLensConfig,
    MediaType,
    ParsedMedia,
    RenameConfig,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """创建 TestClient，每个测试独立。"""
    with TestClient(app) as c:
        yield c


def _mock_config() -> MediaLensConfig:
    """创建一个测试用配置。"""
    return MediaLensConfig(
        tmdb_api_key="test_tmdb_key",
        tmdb_language="zh-CN",
        llm_provider="openai",
        llm_api_key="test_llm_key",
        llm_model="gpt-4o-mini",
        tmdb_confidence_threshold=80,
        llm_confidence_threshold=60,
        dry_run=True,
        organize_mode="hardlink",
        db_path=":memory:",
        data_dir="/tmp/test_media",
        rename=RenameConfig(),
    )


# ============================================================================
# 1. GET /api/stats
# ============================================================================


class TestGetStats:
    """仪表盘统计数据"""

    def test_stats_returns_expected_fields(self, client: TestClient) -> None:
        """验证返回字段与前端期望一致。"""
        mock_db = MagicMock()
        mock_db.get_stats.return_value = {
            "total_matches": 2847,
            "cache_hits": 1952,
            "llm_calls": 147,
            "files_organized": 2623,
            "cache_rate": 68.5,
            "llm_rate": 5.2,
            "recent_activity": 23,
            "cache_hit_rate": 0.685,
            "source_distribution": {"cache_hit": 1952, "tmdb_exact": 748},
            "llm_success_count": 140,
            "file_operation_count": 2623,
            "avg_confidence": 88.5,
        }

        with patch("medialens.api.server._get_db", return_value=mock_db):
            resp = client.get("/api/stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        s = data["data"]
        assert s["total_matches"] == 2847
        assert s["cache_hits"] == 1952
        assert s["llm_calls"] == 147
        assert s["files_organized"] == 2623
        assert s["cache_rate"] == 68.5
        assert s["llm_rate"] == 5.2
        assert s["recent_activity"] == 23

    def test_stats_error_returns_500(self, client: TestClient) -> None:
        """验证数据库异常时返回 500。"""
        mock_db = MagicMock()
        mock_db.get_stats.side_effect = RuntimeError("DB error")

        with patch("medialens.api.server._get_db", return_value=mock_db):
            resp = client.get("/api/stats")

        assert resp.status_code == 500
        assert resp.json()["detail"]["success"] is False


# ============================================================================
# 2. GET /api/matches/recent
# ============================================================================


class TestGetRecentMatches:
    """最近匹配记录"""

    def test_recent_matches_returns_list(self, client: TestClient) -> None:
        """验证返回匹配记录列表。"""
        mock_db = MagicMock()
        mock_db.get_recent_matches.return_value = [
            {
                "id": 1,
                "file_path": "/media/movie.mkv",
                "matched_title": "功夫",
                "matched_year": 2004,
                "tmdb_id": 9470,
                "confidence": 95.0,
                "match_source": "tmdb_exact",
                "file_format": "single_file",
                "overview": "1940年代的上海...",
                "poster_path": "/poster.jpg",
                "parsed_season": None,
                "parsed_episode": None,
                "created_at": "2026-06-05T14:30:00",
            },
        ]

        with patch("medialens.api.server._get_db", return_value=mock_db):
            resp = client.get("/api/matches/recent?limit=5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        items = data["data"]
        assert len(items) == 1
        assert items[0]["title"] == "功夫"
        assert items[0]["tmdb_id"] == 9470
        assert items[0]["source"] == "tmdb_exact"

    def test_recent_matches_empty(self, client: TestClient) -> None:
        """验证无记录时返回空列表。"""
        mock_db = MagicMock()
        mock_db.get_recent_matches.return_value = []

        with patch("medialens.api.server._get_db", return_value=mock_db):
            resp = client.get("/api/matches/recent")

        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ============================================================================
# 3. GET /api/files/pending
# ============================================================================


class TestGetPendingFiles:
    """待处理文件列表"""

    def test_pending_files_empty_when_no_dir(self, client: TestClient) -> None:
        """验证未配置目录时返回空列表。"""
        mock_cfg = _mock_config()
        mock_cfg.data_dir = "/nonexistent/dir"

        with (
            patch("medialens.api.server._get_config", return_value=mock_cfg),
            patch("medialens.api.server._get_db"),
        ):
            resp = client.get("/api/files/pending")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_pending_files_returns_formatted_list(self, client: TestClient) -> None:
        """验证返回格式化文件列表。"""
        mock_cfg = _mock_config()
        mock_cfg.data_dir = "/tmp"

        mock_db = MagicMock()
        mock_db.get_pending_files.return_value = [
            "/tmp/movie.mkv",
            "/tmp/tv_show/BDMV/",
        ]

        with (
            patch("medialens.api.server._get_config", return_value=mock_cfg),
            patch("medialens.api.server._get_db", return_value=mock_db),
            patch("medialens.api.server.get_video_files", return_value=["/tmp/movie.mkv"]),
            patch("medialens.api.server.detect_format", side_effect=[
                FileFormat.SINGLE_FILE,
                FileFormat.BLURAY_BDMV,
            ]),
            patch("pathlib.Path.stat") as mock_stat,
        ):
            mock_stat.return_value.st_size = 1_073_741_824  # 1 GB
            resp = client.get("/api/files/pending")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # get_pending_files 返回的结果会被 FormatDetector 遍历
        assert isinstance(data["data"], list)


# ============================================================================
# 4. GET /api/logs
# ============================================================================


class TestGetLogs:
    """活动日志"""

    def test_logs_returns_list(self, client: TestClient) -> None:
        """验证返回日志列表。"""
        mock_db = MagicMock()
        mock_db.get_logs.return_value = [
            {"time": "14:30:00", "level": "info", "message": "扫描完成"},
            {"time": "14:29:00", "level": "success", "message": "匹配成功"},
        ]

        with patch("medialens.api.server._get_db", return_value=mock_db):
            resp = client.get("/api/logs?level=all&limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_logs_return_empty_when_error(self, client: TestClient) -> None:
        """验证数据库异常时返回空列表（不崩溃）。"""
        mock_db = MagicMock()
        mock_db.get_logs.side_effect = RuntimeError("DB error")

        with patch("medialens.api.server._get_db", return_value=mock_db):
            resp = client.get("/api/logs")

        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ============================================================================
# 5. GET /api/config & POST /api/config
# ============================================================================


class TestGetConfig:
    """获取配置"""

    def test_config_returns_structured_data(self, client: TestClient) -> None:
        """验证返回前端期望的嵌套结构。"""
        with patch("medialens.api.server._get_config", return_value=_mock_config()):
            resp = client.get("/api/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        cfg = data["data"]
        assert "tmdb" in cfg
        assert "llm" in cfg
        assert "organize" in cfg
        assert "db" in cfg
        assert cfg["tmdb"]["api_key"] == "test_tmdb_key"
        assert cfg["llm"]["provider"] == "openai"
        assert cfg["organize"]["mode"] == "hardlink"
        assert cfg["db"]["path"] == ":memory:"


class TestSaveConfig:
    """更新配置"""

    def test_save_config_updates_and_returns(self, client: TestClient) -> None:
        """验证更新配置后返回完整配置。"""
        with patch("medialens.api.server._get_config", return_value=_mock_config()):
            resp = client.post(
                "/api/config",
                json={"tmdb_api_key": "new_key", "llm_model": "gpt-4o"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        cfg = data["data"]
        assert cfg["tmdb"]["api_key"] == "new_key"
        assert cfg["llm"]["model"] == "gpt-4o"  # 已更新
        # 未更新的字段保持不变
        assert cfg["llm"]["provider"] == "openai"

    def test_save_config_partial_update(self, client: TestClient) -> None:
        """验证部分更新只修改指定字段。"""
        with patch("medialens.api.server._get_config", return_value=_mock_config()):
            resp = client.post(
                "/api/config",
                json={"dry_run": False},
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["organize"]["dry_run"] is False


# ============================================================================
# 6. POST /api/scan
# ============================================================================


class TestScanDirectory:
    """扫描目录"""

    def test_scan_nonexistent_path(self, client: TestClient) -> None:
        """验证不存在的路径返回 404。"""
        resp = client.post("/api/scan", json={"path": "/nonexistent"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["success"] is False

    def test_scan_returns_files(self, client: TestClient, tmp_path: Path) -> None:
        """验证扫描返回文件列表。"""
        # 创建临时视频文件
        video = tmp_path / "test_movie.mkv"
        video.write_text("dummy")

        mock_db = MagicMock()
        mock_db.get_pending_files.return_value = [str(video)]

        with (
            patch("medialens.api.server._get_db", return_value=mock_db),
            patch("medialens.api.server.get_video_files", return_value=[str(video)]),
        ):
            resp = client.post("/api/scan", json={"path": str(tmp_path)})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["path"] == str(tmp_path)
        assert isinstance(data["data"]["files"], list)


# ============================================================================
# 7. POST /api/match
# ============================================================================


class TestMatchFile:
    """匹配文件"""

    def test_match_nonexistent_file(self, client: TestClient) -> None:
        """验证不存在的文件返回 404。"""
        resp = client.post("/api/match", json={"path": "/nonexistent.mkv"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["success"] is False

    def test_match_success(self, client: TestClient, tmp_path: Path) -> None:
        """验证成功的匹配返回完整结果。"""
        video = tmp_path / "test.mkv"
        video.write_text("dummy")

        mock_cfg = MediaLensConfig(
            tmdb_api_key="test_key",
            tmdb_language="zh-CN",
            llm_api_key="",
            db_path=":memory:",
        )

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=9470,
            title="功夫",
            original_title="Kung Fu Hustle",
            year=2004,
            source=MatchSource.TMDB_EXACT,
            confidence=95.0,
            overview="1940年代的上海...",
            poster_path="/poster.jpg",
            backdrop_path="/bg.jpg",
            candidates=[],
        )

        with (
            patch("medialens.api.server._get_config", return_value=mock_cfg),
            patch("medialens.matcher.matcher_pipeline.MatcherPipeline.match",
                  return_value=mock_result),
        ):
            resp = client.post("/api/match", json={"path": str(video)})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        result = data["data"]
        assert result["matched"] is True
        assert result["title"] == "功夫"
        assert result["tmdb_id"] == 9470
        assert result["confidence"] == 95.0

    def test_match_no_tmdb_key(self, client: TestClient, tmp_path: Path) -> None:
        """验证无 TMDB Key 时返回 400。"""
        video = tmp_path / "test.mkv"
        video.write_text("dummy")

        mock_cfg = MediaLensConfig(tmdb_api_key="", db_path=":memory:")

        with patch("medialens.api.server._get_config", return_value=mock_cfg):
            resp = client.post("/api/match", json={"path": str(video)})

        assert resp.status_code == 400
        assert resp.json()["detail"]["success"] is False


# ============================================================================
# 8. POST /api/organize
# ============================================================================


class TestOrganizeFile:
    """整理文件"""

    def test_organize_nonexistent_file(self, client: TestClient) -> None:
        """验证不存在的文件返回 404。"""
        resp = client.post("/api/organize", json={
            "path": "/nonexistent.mkv",
            "target_dir": "/output",
        })
        assert resp.status_code == 404
        assert resp.json()["detail"]["success"] is False

    def test_organize_dry_run(self, client: TestClient, tmp_path: Path) -> None:
        """验证 dry_run 模式返回预览结果。"""
        video = tmp_path / "test.mkv"
        video.write_text("dummy")
        output = tmp_path / "output"
        output.mkdir()

        mock_cfg = MediaLensConfig(
            tmdb_api_key="test_key",
            tmdb_language="zh-CN",
            db_path=":memory:",
            llm_api_key="",
        )

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=9470,
            title="功夫",
            original_title="Kung Fu Hustle",
            year=2004,
            source=MatchSource.TMDB_EXACT,
            confidence=95.0,
        )

        with (
            patch("medialens.api.server._get_config", return_value=mock_cfg),
            patch("medialens.api.server._get_db") as mock_db_get,
            patch("medialens.matcher.matcher_pipeline.MatcherPipeline.match",
                  return_value=mock_result),
        ):
            mock_db = MagicMock()
            mock_db_get.return_value = mock_db
            resp = client.post("/api/organize", json={
                "path": str(video),
                "target_dir": str(output),
                "dry_run": True,
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        op = data["data"]
        assert op["operation"] in ("hardlink", "copy", "move")
        assert op["dry_run"] is True


# ============================================================================
# 9. 响应格式统一性
# ============================================================================


class TestResponseFormat:
    """验证所有端点响应格式一致。"""

    ENDPOINTS = [
        ("GET", "/api/stats"),
        ("GET", "/api/matches/recent"),
        ("GET", "/api/files/pending"),
        ("GET", "/api/logs"),
        ("GET", "/api/config"),
    ]

    def test_all_get_endpoints_have_unified_format(self, client: TestClient) -> None:
        """验证所有 GET 端点返回统一格式。"""
        mock_db = MagicMock()
        mock_db.get_stats.return_value = {"total_matches": 0, "cache_hits": 0}
        mock_db.get_recent_matches.return_value = []
        mock_db.get_logs.return_value = []
        mock_db.get_pending_files.return_value = []

        mock_cfg = _mock_config()
        mock_cfg.data_dir = "/nonexistent"

        with (
            patch("medialens.api.server._get_db", return_value=mock_db),
            patch("medialens.api.server._get_config", return_value=mock_cfg),
        ):
            for method, path in self.ENDPOINTS:
                if method == "GET":
                    resp = client.get(path)
                else:
                    continue

                assert resp.status_code in (200,), f"{method} {path} failed"
                body = resp.json()
                # 统一格式：成功时 {"success": true, "data": ...}
                assert "success" in body, f"{path} 缺少 success 字段"
