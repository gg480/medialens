"""
MediaLens 批量编排引擎单元测试

使用临时文件和 mock 对象测试 BatchEngine 的所有核心功能。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaLensConfig,
    MediaType,
    ParsedMedia,
)
from medialens.orchestrator.batch_engine import (
    BatchEngine,
    BatchOptions,
    BatchResult,
    FileInfo,
    FileResult,
    ScanResult,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def temp_db() -> str:
    """创建临时数据库路径。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    yield tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


@pytest.fixture
def config() -> MediaLensConfig:
    """创建测试用配置。"""
    return MediaLensConfig(
        tmdb_api_key="test_key",
        tmdb_language="zh-CN",
        dry_run=True,
        db_path=":memory:",
    )


@pytest.fixture
def temp_dir() -> str:
    """创建临时目录，内含测试视频文件。"""
    d = tempfile.mkdtemp()
    # 创建一些测试文件
    for name in ["Test Movie (2020).mkv", "Another Film (2019).mp4", "第三部电影 (2021).mkv"]:
        p = Path(d) / name
        p.write_text("dummy video content")
        # 模拟不同大小的文件
        if "Another" in name:
            # 调整文件大小
            with open(p, "wb") as f:
                f.write(b"x" * 1024 * 10)  # 10KB
        elif "第三部" in name:
            with open(p, "wb") as f:
                f.write(b"x" * 1024 * 5)  # 5KB
        else:
            with open(p, "wb") as f:
                f.write(b"x" * 1024 * 20)  # 20KB

    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def engine(config: MediaLensConfig) -> BatchEngine:
    """创建 BatchEngine 实例（使用临时数据库文件）。"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    eng = BatchEngine(config=config, db_path=tmp.name)
    yield eng
    try:
        eng.db.close()
        os.unlink(tmp.name)
    except OSError:
        pass


# ------------------------------------------------------------------
# 测试数据类
# ------------------------------------------------------------------

class TestDataClasses:
    """验证数据类的结构和默认值。"""

    def test_file_info(self) -> None:
        fi = FileInfo(path="/test.mkv", format=FileFormat.SINGLE_FILE, size=1024, modified_time=100.0)
        assert fi.path == "/test.mkv"
        assert fi.format == FileFormat.SINGLE_FILE
        assert fi.size == 1024

    def test_scan_result(self) -> None:
        fi = FileInfo(path="/test.mkv", format=FileFormat.SINGLE_FILE, size=1024, modified_time=100.0)
        sr = ScanResult(total=1, new_files=[fi], existing_files=0, errors=[])
        assert sr.total == 1
        assert len(sr.new_files) == 1

    def test_batch_options_defaults(self) -> None:
        opts = BatchOptions()
        assert opts.scrape is True
        assert opts.organize is False
        assert opts.dry_run is True
        assert opts.max_workers == 2
        assert opts.continue_on_error is True
        assert opts.llm_enabled is True

    def test_file_result_defaults(self) -> None:
        fr = FileResult(path="/test.mkv", matched=True)
        assert fr.title == ""
        assert fr.year is None
        assert fr.confidence == 0.0
        assert fr.error is None
        assert fr.nfo_generated is False

    def test_batch_result(self) -> None:
        fr = FileResult(path="/test.mkv", matched=True)
        br = BatchResult(total=1, succeeded=1, failed=0, skipped=0, results=[fr], duration=1.5)
        assert br.total == 1
        assert br.succeeded == 1
        assert br.duration == 1.5


# ------------------------------------------------------------------
# 测试扫描目录
# ------------------------------------------------------------------

class TestScanDirectory:
    """测试目录扫描功能。"""

    def test_scan_directory(self, engine: BatchEngine, temp_dir: str) -> None:
        """扫描目录应返回所有视频文件。"""
        result = engine.scan_directory(temp_dir)
        assert result.total == 3
        assert len(result.new_files) == 3
        assert result.existing_files == 0
        assert len(result.errors) == 0

    def test_scan_directory_empty(self, engine: BatchEngine) -> None:
        """扫描空目录应返回空结果。"""
        empty_dir = tempfile.mkdtemp()
        try:
            result = engine.scan_directory(empty_dir)
            assert result.total == 0
            assert len(result.new_files) == 0
        finally:
            os.rmdir(empty_dir)

    def test_scan_directory_not_found(self, engine: BatchEngine) -> None:
        """扫描不存在的路径应返回错误。"""
        result = engine.scan_directory("/nonexistent/path")
        assert result.total == 0
        assert len(result.errors) > 0

    def test_scan_directory_sorted_by_size(self, engine: BatchEngine, temp_dir: str) -> None:
        """文件应按大小降序排列。"""
        result = engine.scan_directory(temp_dir)
        sizes = [fi.size for fi in result.new_files]
        assert sizes == sorted(sizes, reverse=True), "文件应按大小降序排列"

    def test_scan_directory_single_file(self, engine: BatchEngine, temp_dir: str) -> None:
        """扫描单个文件路径。"""
        file_path = str(Path(temp_dir) / "Test Movie (2020).mkv")
        result = engine.scan_directory(file_path)
        assert result.total == 1
        assert len(result.new_files) == 1
        assert result.new_files[0].path == file_path

    def test_scan_directory_existing_files_skipped(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """已匹配的文件应被跳过。"""
        # 先手动插入一条匹配记录
        parsed = ParsedMedia(
            raw_path=Path(temp_dir) / "Test Movie (2020).mkv",
            raw_filename="Test Movie (2020).mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="Test Movie",
            year=2020,
        )
        match_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )
        engine.db.save_match(parsed, match_result)

        # 扫描目录，应跳过已匹配的文件
        result = engine.scan_directory(temp_dir)
        assert result.total == 3
        assert len(result.new_files) == 2  # 跳过已匹配的
        assert result.existing_files == 1

    def test_scan_returns_fileinfo_with_format(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """FileInfo 应包含正确的格式信息。"""
        result = engine.scan_directory(temp_dir)
        for fi in result.new_files:
            assert fi.format in (
                FileFormat.SINGLE_FILE, FileFormat.TV_EPISODE, FileFormat.UNKNOWN,
            )
            assert fi.modified_time > 0


# ------------------------------------------------------------------
# 测试批量处理
# ------------------------------------------------------------------

class TestRunBatch:
    """测试批量处理功能。"""

    def test_run_batch_single_file(self, engine: BatchEngine, temp_dir: str) -> None:
        """处理单个文件应返回结果。"""
        file_path = str(Path(temp_dir) / "Test Movie (2020).mkv")

        # Mock MatcherPipeline.match 以返回模拟结果
        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, organize=False, dry_run=True)

            result = engine.run_batch([file_path], options=opts)

        assert result.total == 1
        assert result.succeeded == 1
        assert result.failed == 0
        assert result.skipped == 0
        assert len(result.results) == 1
        assert result.results[0].matched is True
        assert result.results[0].title == "Test Movie"
        assert result.results[0].year == 2020
        assert result.results[0].confidence == 98.0

    def test_run_batch_multiple_files(self, engine: BatchEngine, temp_dir: str) -> None:
        """批量处理多个文件。"""
        files = [
            str(Path(temp_dir) / "Test Movie (2020).mkv"),
            str(Path(temp_dir) / "Another Film (2019).mp4"),
        ]

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, organize=False, dry_run=True, max_workers=1)

            result = engine.run_batch(files, options=opts)

        assert result.total == 2
        assert result.succeeded == 2
        assert result.failed == 0

    def test_run_batch_with_unmatched(self, engine: BatchEngine, temp_dir: str) -> None:
        """未匹配的文件应正确记录。"""
        file_path = str(Path(temp_dir) / "Test Movie (2020).mkv")

        mock_result = MatchResult(
            matched=False,
            media_type=MediaType.MOVIE,
            tmdb_id=0,
            title="Unknown",
            original_title="",
            source=MatchSource.TMDB_FUZZY,
            confidence=0.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, organize=False, dry_run=True)

            result = engine.run_batch([file_path], options=opts)

        assert result.total == 1
        assert result.succeeded == 1  # 未匹配不算失败
        assert result.results[0].matched is False
        assert result.results[0].title == ""

    def test_batch_options_scrape_and_organize(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """测试刮削和整理选项。"""
        file_path = str(Path(temp_dir) / "Test Movie (2020).mkv")

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline, \
             patch.object(engine, "_get_nfo") as mock_nfo, \
             patch.object(engine, "_get_fm") as mock_fm:

            mock_pipeline.return_value.match.return_value = mock_result
            mock_nfo_instance = MagicMock()
            mock_nfo.return_value = mock_nfo_instance
            # 让 fm.organize_file 返回一个有 success=True 的简单对象
            mock_fm_instance = MagicMock()
            op_result = MagicMock()
            op_result.success = True
            op_result.operation = "hardlink"
            mock_fm_instance.organize_file.return_value = op_result
            mock_fm.return_value = mock_fm_instance

            opts = BatchOptions(
                scrape=True,
                organize=True,
                target_dir=temp_dir,
                dry_run=True,
                max_workers=1,
            )

            result = engine.run_batch([file_path], options=opts)

        assert result.succeeded == 1
        mock_nfo_instance.generate_movie_nfo.assert_called_once()

    def test_batch_with_parallel_workers(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """多线程并行处理。"""
        files = [
            str(Path(temp_dir) / "Test Movie (2020).mkv"),
            str(Path(temp_dir) / "Another Film (2019).mp4"),
            str(Path(temp_dir) / "第三部电影 (2021).mkv"),
        ]

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, organize=False, dry_run=True, max_workers=3)

            result = engine.run_batch(files, options=opts)

        assert result.total == 3
        assert result.succeeded == 3
        assert result.duration > 0


# ------------------------------------------------------------------
# 测试断点续传
# ------------------------------------------------------------------

class TestCheckpoint:
    """测试断点续传功能。"""

    def test_checkpoint_save_and_load(self, engine: BatchEngine) -> None:
        """保存和加载 checkpoint。"""
        job_id = "test_job_001"

        # 保存 checkpoint
        success = engine.save_checkpoint(job_id, ["file1.mkv", "file2.mkv"], {"count": 2})
        assert success is True

        # 加载 checkpoint
        checkpoint = engine.load_checkpoint(job_id)
        assert checkpoint is not None
        assert job_id in checkpoint["job_id"]
        assert len(checkpoint["done"]) == 2
        assert "file1.mkv" in checkpoint["done"]
        assert "file2.mkv" in checkpoint["done"]

    def test_checkpoint_empty_returns_none(self, engine: BatchEngine) -> None:
        """不存在的 job_id 应返回 None。"""
        checkpoint = engine.load_checkpoint("nonexistent_job")
        assert checkpoint is None

    def test_checkpoint_partial_completion(self, engine: BatchEngine) -> None:
        """部分完成的 checkpoint。"""
        job_id = "partial_job"

        # 手动通过 db 设置 checkpoint
        engine.db.save_checkpoint(job_id, "done_file.mkv", "done")
        engine.db.save_checkpoint(job_id, "failed_file.mkv", "failed")
        engine.db.save_checkpoint(job_id, "pending_file.mkv", "pending")

        checkpoint = engine.load_checkpoint(job_id)
        assert checkpoint is not None
        assert len(checkpoint["done"]) == 1
        assert len(checkpoint["failed"]) == 1
        assert checkpoint["total"] == 2

    def test_resume_from_checkpoint(self, engine: BatchEngine, temp_dir: str) -> None:
        """断点续传应跳过已完成的文件。"""
        job_id = "resume_job"

        # 创建测试文件
        file1 = str(Path(temp_dir) / "Test Movie (2020).mkv")
        file2 = str(Path(temp_dir) / "Another Film (2019).mp4")

        # 标记 file1 为已完成
        engine.db.save_checkpoint(job_id, file1, "done")

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, organize=False, dry_run=True, max_workers=1)

            # 执行续传
            result = engine.run_batch([file1, file2], options=opts, job_id=job_id)

        # file1 被跳过（skipped），file2 被处理
        assert result.skipped == 1
        assert result.succeeded == 1  # 只有 file2 被处理

    def test_resume_from_checkpoint_failed_files(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """断点续传应重新处理失败的文件。"""
        job_id = "retry_job"

        file1 = str(Path(temp_dir) / "Test Movie (2020).mkv")
        file2 = str(Path(temp_dir) / "Another Film (2019).mp4")

        # 标记 file1 失败，file2 完成
        engine.db.save_checkpoint(job_id, file1, "failed")
        engine.db.save_checkpoint(job_id, file2, "done")

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, organize=False, dry_run=True, max_workers=1)

            result = engine.run_batch([file1, file2], options=opts, job_id=job_id)

        # file1 被重试（failed 不算 skipped），file2 被跳过
        assert result.skipped == 1  # 只有 file2 被跳过
        assert result.succeeded == 1  # file1 被重新处理

    def test_list_jobs(self, engine: BatchEngine) -> None:
        """列出任务记录。"""
        # 创建几个任务
        engine.db.create_job("job_1", 10, {"scrape": True})
        engine.db.create_job("job_2", 20, {"scrape": False})

        jobs = engine.list_jobs()
        assert len(jobs) == 2
        job_ids = [j["job_id"] for j in jobs]
        assert "job_1" in job_ids
        assert "job_2" in job_ids

    def test_checkpoint_db_tables_created(self, engine: BatchEngine) -> None:
        """checkpoint 表应自动创建。"""
        # BatchEngine 初始化时已创建表
        result = engine.db._query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_checkpoints'"
        )
        assert result is not None
        assert result["name"] == "batch_checkpoints"

        result = engine.db._query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_jobs'"
        )
        assert result is not None
        assert result["name"] == "batch_jobs"


# ------------------------------------------------------------------
# 测试错误处理
# ------------------------------------------------------------------

class TestErrorHandling:
    """测试错误处理机制。"""

    def test_error_handling_continues(self, engine: BatchEngine, temp_dir: str) -> None:
        """continue_on_error=True 时，单个文件失败不应影响整体。"""
        files = [
            str(Path(temp_dir) / "Test Movie (2020).mkv"),
            str(Path(temp_dir) / "Another Film (2019).mp4"),
        ]

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline_instance = MagicMock()
            mock_pipeline.return_value = mock_pipeline_instance

            # 第一个文件抛出异常，第二个返回正常
            mock_pipeline_instance.match.side_effect = [
                ValueError("API error"),
                MatchResult(
                    matched=True,
                    media_type=MediaType.MOVIE,
                    tmdb_id=123,
                    title="Another Film",
                    original_title="Another Film",
                    year=2019,
                    source=MatchSource.TMDB_EXACT,
                    confidence=95.0,
                ),
            ]

            opts = BatchOptions(
                scrape=False, organize=False, dry_run=True,
                max_workers=1, continue_on_error=True,
            )

            result = engine.run_batch(files, options=opts)

        assert result.total == 2
        assert result.succeeded == 1
        assert result.failed == 1
        # 失败的文件应有错误信息
        failed_results = [r for r in result.results if r.error]
        assert len(failed_results) == 1
        assert "API error" in failed_results[0].error

    def test_error_handling_timeout_protection(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """文件处理超时应被捕获。"""
        file_path = str(Path(temp_dir) / "Test Movie (2020).mkv")

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.side_effect = TimeoutError("处理超时")

            opts = BatchOptions(scrape=False, dry_run=True, max_workers=1)

            result = engine.run_batch([file_path], options=opts)

        assert result.total == 1
        assert result.failed == 1
        assert result.results[0].error is not None

    def test_cancel_batch(self, engine: BatchEngine, temp_dir: str) -> None:
        """取消正在运行的任务应停止后续处理。"""
        import threading
        import time

        file1 = str(Path(temp_dir) / "Test Movie (2020).mkv")
        file2 = str(Path(temp_dir) / "Another Film (2019).mp4")

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, dry_run=True, max_workers=1)

            # 在另一个线程中启动 batch，然后在 100ms 后取消
            result_holder: list[BatchResult] = []

            def _run():
                r = engine.run_batch([file1, file2], options=opts)
                result_holder.append(r)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            time.sleep(0.05)
            engine.cancel()
            t.join(timeout=5)

            assert len(result_holder) == 1
            # 取消后至少有一个文件被处理（已经在处理中的），但总文件数正确
            assert result_holder[0].total == 2

    def test_batch_result_duration_positive(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """批量处理耗时应为正数。"""
        file_path = str(Path(temp_dir) / "Test Movie (2020).mkv")

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, dry_run=True, max_workers=1)

            result = engine.run_batch([file_path], options=opts)

        assert result.duration > 0


# ------------------------------------------------------------------
# 测试 run_batch_from_directory
# ------------------------------------------------------------------

class TestRunBatchFromDirectory:
    """测试 run_batch_from_directory 一步到位接口。"""

    def test_run_from_directory(self, engine: BatchEngine, temp_dir: str) -> None:
        """从目录启动批量处理。"""
        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, dry_run=True, max_workers=1)

            result = engine.run_batch_from_directory(temp_dir, options=opts)

        assert result.total == 3
        assert result.succeeded == 3

    def test_run_from_directory_empty(self, engine: BatchEngine) -> None:
        """空目录启动批量处理应返回空结果。"""
        empty_dir = tempfile.mkdtemp()
        try:
            result = engine.run_batch_from_directory(empty_dir)
            assert result.total == 0
            assert result.duration == 0.0
        finally:
            os.rmdir(empty_dir)

    def test_run_from_directory_skips_existing(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """从目录启动应跳过已匹配文件。"""
        # 先标记一个文件为已匹配
        parsed = ParsedMedia(
            raw_path=Path(temp_dir) / "Test Movie (2020).mkv",
            raw_filename="Test Movie (2020).mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="Test Movie",
            year=2020,
        )
        match_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )
        engine.db.save_match(parsed, match_result)

        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test",
            original_title="Test",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, dry_run=True, max_workers=1)

            result = engine.run_batch_from_directory(temp_dir, options=opts)

        assert result.total == 2  # 只有2个新文件

    def test_run_from_directory_preserves_metadata(
        self, engine: BatchEngine, temp_dir: str,
    ) -> None:
        """处理后应能在数据库中找到任务记录。"""
        mock_result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=123,
            title="Test Movie",
            original_title="Test Movie",
            year=2020,
            source=MatchSource.TMDB_EXACT,
            confidence=98.0,
        )

        with patch.object(engine, "_get_pipeline") as mock_pipeline:
            mock_pipeline.return_value.match.return_value = mock_result
            opts = BatchOptions(scrape=False, dry_run=True, max_workers=1)

            result = engine.run_batch_from_directory(temp_dir, options=opts)

        # 数据库中应有任务记录
        jobs = engine.list_jobs()
        assert len(jobs) >= 1
        assert jobs[0]["total_files"] == 3
