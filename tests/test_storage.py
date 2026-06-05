"""
MediaLens 存储层单元测试

使用临时文件模拟 SQLite 数据库，测试所有 CRUD 方法。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaType,
    ParsedMedia,
)
from medialens.storage.database import DatabaseManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def db() -> DatabaseManager:
    """创建一个使用临时文件的 DatabaseManager 实例，测试结束后清理。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    manager = DatabaseManager(tmp.name)
    manager.initialize()
    yield manager
    manager.close()
    os.unlink(tmp.name)


@pytest.fixture
def sample_parsed() -> ParsedMedia:
    """创建一个 ParsedMedia 示例用于测试。"""
    return ParsedMedia(
        raw_path=Path("/media/movies/The Matrix (1999).mkv"),
        raw_filename="The Matrix (1999).mkv",
        file_format=FileFormat.SINGLE_FILE,
        title="The Matrix",
        year=1999,
        source="BluRay",
        resolution="1080p",
        video_codec="h264",
        audio_codec="dts",
    )


@pytest.fixture
def sample_result() -> MatchResult:
    """创建一个 MatchResult 示例用于测试。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.MOVIE,
        tmdb_id=603,
        title="The Matrix",
        original_title="The Matrix",
        year=1999,
        source=MatchSource.TMDB_EXACT,
        confidence=95.0,
        overview="A computer hacker learns about the true nature of reality.",
        poster_path="/poster/matrix.jpg",
        backdrop_path="/backdrop/matrix.jpg",
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestInitialize:
    """表结构初始化测试"""

    def test_initialize_creates_tables(self, db: DatabaseManager) -> None:
        """验证 initialize() 创建了预期的三张表。"""
        c = db.conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in c.fetchall()}

        assert "match_history" in tables
        assert "llm_call_log" in tables
        assert "file_operations" in tables

    def test_initialize_is_idempotent(self, db: DatabaseManager) -> None:
        """验证多次调用 initialize() 不会报错。"""
        db.initialize()  # 第二次调用
        db.initialize()  # 第三次调用
        c = db.conn.cursor()
        c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        assert c.fetchone()[0] >= 3


class TestSaveAndGetMatch:
    """保存与查询匹配记录"""

    def test_save_and_get_match(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证保存后能通过 id 完整取出。"""
        match_id = db.save_match(sample_parsed, sample_result, file_hash="abc123")
        assert match_id > 0

        record = db.get_match_by_id(match_id)
        assert record is not None
        assert record["file_path"] == str(sample_parsed.raw_path)
        assert record["file_hash"] == "abc123"
        assert record["file_format"] == FileFormat.SINGLE_FILE.value
        assert record["parsed_title"] == "The Matrix"
        assert record["parsed_year"] == 1999
        assert record["tmdb_id"] == 603
        assert record["media_type"] == MediaType.MOVIE.value
        assert record["matched_title"] == "The Matrix"
        assert record["match_source"] == MatchSource.TMDB_EXACT.value
        assert record["confidence"] == 95.0
        assert "overview" in record

    def test_get_match_by_path(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证通过文件路径查询。"""
        db.save_match(sample_parsed, sample_result)
        record = db.get_match_by_path(str(sample_parsed.raw_path))
        assert record is not None
        assert record["parsed_title"] == "The Matrix"

    def test_get_match_by_path_not_found(self, db: DatabaseManager) -> None:
        """验证查询不存在的路径返回 None。"""
        record = db.get_match_by_path("/nonexistent/movie.mkv")
        assert record is None

    def test_get_match_by_hash(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证通过文件哈希查询。"""
        db.save_match(sample_parsed, sample_result, file_hash="hash_xyz")
        record = db.get_match_by_hash("hash_xyz")
        assert record is not None
        assert record["parsed_title"] == "The Matrix"

    def test_get_match_by_hash_not_found(self, db: DatabaseManager) -> None:
        """验证查询不存在的哈希返回 None。"""
        record = db.get_match_by_hash("nonexistent_hash")
        assert record is None

    def test_save_upsert_same_path(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证同路径保存两次会更新而非插入重复记录。"""
        db.save_match(sample_parsed, sample_result)

        # 修改标题后再次保存（同路径）
        result2 = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=603,
            title="The Matrix Reloaded",
            original_title="The Matrix Reloaded",
            year=2003,
            source=MatchSource.USER_CONFIRMED,
            confidence=100.0,
        )
        db.save_match(sample_parsed, result2)

        # 验证只有一条记录且内容已更新
        c = db.conn.cursor()
        c.execute("SELECT COUNT(*) FROM match_history")
        assert c.fetchone()[0] == 1

        record = db.get_match_by_path(str(sample_parsed.raw_path))
        assert record is not None
        assert record["matched_title"] == "The Matrix Reloaded"
        assert record["match_source"] == MatchSource.USER_CONFIRMED.value
        assert record["matched_year"] == 2003


class TestUpdateMatch:
    """更新匹配记录"""

    def test_update_match(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证更新字段后值已变更。"""
        match_id = db.save_match(sample_parsed, sample_result)

        updated = db.update_match(match_id, {"confidence": 100.0, "match_source": MatchSource.USER_CONFIRMED.value})
        assert updated is True

        record = db.get_match_by_id(match_id)
        assert record is not None
        assert record["confidence"] == 100.0
        assert record["match_source"] == MatchSource.USER_CONFIRMED.value

    def test_update_match_not_found(self, db: DatabaseManager) -> None:
        """验证更新不存在的 id 返回 False。"""
        updated = db.update_match(99999, {"confidence": 50.0})
        assert updated is False

    def test_update_match_empty_updates(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证传入空字典返回 False。"""
        match_id = db.save_match(sample_parsed, sample_result)
        updated = db.update_match(match_id, {})
        assert updated is False

    def test_update_match_ignores_invalid_keys(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证传入不允许的字段名会被忽略。"""
        match_id = db.save_match(sample_parsed, sample_result)
        updated = db.update_match(match_id, {"nonexistent_field": "value"})
        assert updated is False


class TestDeleteMatch:
    """删除匹配记录"""

    def test_delete_match(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证删除后查询不到。"""
        match_id = db.save_match(sample_parsed, sample_result)
        deleted = db.delete_match(match_id)
        assert deleted is True

        record = db.get_match_by_id(match_id)
        assert record is None

    def test_delete_match_not_found(self, db: DatabaseManager) -> None:
        """验证删除不存在的 id 返回 False。"""
        deleted = db.delete_match(99999)
        assert deleted is False


class TestLogLlmCall:
    """LLM 调用日志"""

    def test_log_llm_call(self, db: DatabaseManager) -> None:
        """验证记录 LLM 调用日志。"""
        log_id = db.log_llm_call(
            file_path="/media/movie.mkv",
            prompt="Identify this movie",
            response="The Matrix",
            model="gpt-4o-mini",
            tokens_in=150,
            tokens_out=20,
            duration_ms=1200,
            success=True,
        )
        assert log_id > 0

        c = db.conn.cursor()
        row = c.execute("SELECT * FROM llm_call_log WHERE id = ?", (log_id,)).fetchone()
        assert row is not None
        assert row["file_path"] == "/media/movie.mkv"
        assert row["model"] == "gpt-4o-mini"
        assert row["tokens_in"] == 150
        assert row["tokens_out"] == 20
        assert row["duration_ms"] == 1200
        assert row["success"] == 1

    def test_log_llm_call_failure(self, db: DatabaseManager) -> None:
        """验证记录失败的 LLM 调用。"""
        log_id = db.log_llm_call(
            file_path="/media/movie.mkv",
            prompt="Identify this movie",
            response="API Error: timeout",
            model="gpt-4o-mini",
            tokens_in=100,
            tokens_out=0,
            duration_ms=30000,
            success=False,
        )
        assert log_id > 0

        c = db.conn.cursor()
        row = c.execute("SELECT success FROM llm_call_log WHERE id = ?", (log_id,)).fetchone()
        assert row is not None
        assert row["success"] == 0  # 存储为 0（False 的整数值）


class TestLogFileOperation:
    """文件操作日志"""

    def test_log_file_operation(self, db: DatabaseManager, sample_parsed: ParsedMedia, sample_result: MatchResult) -> None:
        """验证记录文件操作。"""
        match_id = db.save_match(sample_parsed, sample_result)

        op_id = db.log_file_operation(
            source="/media/source.mkv",
            target="/media/target.mkv",
            operation="hardlink",
            dry_run=True,
            success=True,
            match_id=match_id,
        )
        assert op_id > 0

        c = db.conn.cursor()
        row = c.execute("SELECT * FROM file_operations WHERE id = ?", (op_id,)).fetchone()
        assert row is not None
        assert row["source_path"] == "/media/source.mkv"
        assert row["target_path"] == "/media/target.mkv"
        assert row["operation"] == "hardlink"
        assert row["dry_run"] == 1
        assert row["success"] == 1
        assert row["match_history_id"] == match_id

    def test_log_file_operation_with_error(self, db: DatabaseManager) -> None:
        """验证记录失败的文件操作（含错误信息）。"""
        op_id = db.log_file_operation(
            source="/media/src.mkv",
            target="/media/dst.mkv",
            operation="copy",
            dry_run=False,
            success=False,
            error="Disk full",
        )
        assert op_id > 0

        c = db.conn.cursor()
        row = c.execute("SELECT error, success FROM file_operations WHERE id = ?", (op_id,)).fetchone()
        assert row is not None
        assert row["error"] == "Disk full"
        assert row["success"] == 0


class TestGetStats:
    """统计信息"""

    def test_get_stats_empty(self, db: DatabaseManager) -> None:
        """验证空数据库的统计信息。"""
        stats = db.get_stats()
        assert stats["total_matches"] == 0
        assert stats["cache_hits"] == 0
        assert stats["cache_hit_rate"] == 0.0
        assert stats["llm_calls"] == 0
        assert stats["file_operation_count"] == 0
        assert stats["avg_confidence"] == 0.0

    def test_get_stats_with_data(self, db: DatabaseManager) -> None:
        """验证有数据时的统计信息。"""
        # 插入 3 条匹配记录（2 条 TMDB_EXACT，1 条 CACHE_HIT）
        for i in range(3):
            source = MatchSource.CACHE_HIT if i == 0 else MatchSource.TMDB_EXACT
            parsed = ParsedMedia(
                raw_path=Path(f"/media/movie_{i}.mkv"),
                raw_filename=f"movie_{i}.mkv",
                file_format=FileFormat.SINGLE_FILE,
                title=f"Movie {i}",
                year=2000 + i,
            )
            result = MatchResult(
                matched=True,
                media_type=MediaType.MOVIE,
                tmdb_id=100 + i,
                title=f"Movie {i}",
                original_title=f"Movie {i}",
                year=2000 + i,
                source=source,
                confidence=85.0 + i * 5,
            )
            db.save_match(parsed, result)

        # 插入 2 条 LLM 调用日志
        for i in range(2):
            db.log_llm_call(
                file_path=f"/media/movie_{i}.mkv",
                prompt="Identify movie",
                response=f"Movie {i}",
                model="gpt-4o-mini",
                tokens_in=100,
                tokens_out=10,
                duration_ms=500,
                success=True,
            )

        # 插入 1 条文件操作
        db.log_file_operation(
            source="/media/src.mkv",
            target="/media/dst.mkv",
            operation="hardlink",
            dry_run=False,
            success=True,
        )

        stats = db.get_stats()
        assert stats["total_matches"] == 3
        assert stats["cache_hits"] == 1
        # cache_hit_rate = round(1/3, 4) = 0.3333
        assert stats["cache_hit_rate"] == pytest.approx(1 / 3, rel=1e-3)
        assert stats["source_distribution"] == {
            MatchSource.CACHE_HIT.value: 1,
            MatchSource.TMDB_EXACT.value: 2,
        }
        assert stats["llm_calls"] == 2
        assert stats["llm_success_count"] == 2
        assert stats["file_operation_count"] == 1
        # 平均置信度: (85 + 90 + 95) / 3 = 90
        assert stats["avg_confidence"] == 90.0


class TestEdgeCases:
    """边界情况"""

    def test_save_match_unmatched_result(self, db: DatabaseManager) -> None:
        """验证匹配失败（matched=False）时 tmdb_id 为 None。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/unknown.mkv"),
            raw_filename="unknown.mkv",
            file_format=FileFormat.UNKNOWN,
        )
        result = MatchResult(
            matched=False,
            media_type=MediaType.MOVIE,
            tmdb_id=0,
            title="",
            original_title="",
            source=MatchSource.TMDB_FUZZY,
            confidence=0.0,
        )
        match_id = db.save_match(parsed, result)
        record = db.get_match_by_id(match_id)
        assert record is not None
        assert record["tmdb_id"] is None
        assert record["confidence"] == 0.0

    def test_multiple_matches(self, db: DatabaseManager) -> None:
        """验证保存多条记录后能正确查询。"""
        movies = [
            (ParsedMedia(raw_path=Path("/a.mkv"), raw_filename="a.mkv", file_format=FileFormat.SINGLE_FILE, title="A"),
             MatchResult(matched=True, media_type=MediaType.MOVIE, tmdb_id=1, title="A", original_title="A", source=MatchSource.TMDB_EXACT, confidence=90)),
            (ParsedMedia(raw_path=Path("/b.mkv"), raw_filename="b.mkv", file_format=FileFormat.SINGLE_FILE, title="B"),
             MatchResult(matched=True, media_type=MediaType.MOVIE, tmdb_id=2, title="B", original_title="B", source=MatchSource.TMDB_EXACT, confidence=95)),
        ]
        for parsed, result in movies:
            db.save_match(parsed, result)

        c = db.conn.cursor()
        c.execute("SELECT COUNT(*) FROM match_history")
        assert c.fetchone()[0] == 2

    def test_context_manager_reuse(self, db: DatabaseManager) -> None:
        """验证连接复用（多次操作使用同一连接）。"""
        conn1 = db.conn
        conn2 = db.conn
        assert conn1 is conn2
