"""
MediaLens 批量任务编排引擎

协调多文件的自动化处理流程：扫描 → 匹配 → 刮削 → 整理。
支持断点续传（因异常中断后能从中断处继续）。

设计原则：
- 基于 MatcherPipeline 等已有模块，不重新实现匹配逻辑
- 使用 concurrent.futures.ThreadPoolExecutor 实现并行处理
- 每个文件处理带超时保护
- 进度通过数据库实时更新
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from medialens.detector.format_detector import detect_format, get_video_files
from medialens.filemgr.file_manager import FileManager
from medialens.matcher.matcher_pipeline import MatcherPipeline
from medialens.models import (
    FileFormat,
    MatchResult,
    MediaLensConfig,
    MediaType,
    RenameConfig,
)
from medialens.scraper.nfo_generator import NfoGenerator
from medialens.storage.database import DatabaseManager

logger = logging.getLogger(__name__)

# 默认超时时间（秒/文件）
_DEFAULT_FILE_TIMEOUT = 300


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    """扫描到的文件信息"""
    path: str
    format: FileFormat
    size: int
    modified_time: float


@dataclass
class ScanResult:
    """扫描目录的结果"""
    total: int
    new_files: list[FileInfo]
    existing_files: int
    errors: list[str]


@dataclass
class BatchOptions:
    """批量处理选项"""
    scrape: bool = True
    organize: bool = False
    target_dir: str = ""
    dry_run: bool = True
    max_workers: int = 2
    continue_on_error: bool = True
    llm_enabled: bool = True
    file_timeout: int = _DEFAULT_FILE_TIMEOUT


@dataclass
class FileResult:
    """单个文件的处理结果"""
    path: str
    matched: bool
    title: str = ""
    year: Optional[int] = None
    confidence: float = 0.0
    source: str = ""
    nfo_generated: bool = False
    organized: bool = False
    error: Optional[str] = None


@dataclass
class BatchResult:
    """批量处理的结果汇总"""
    total: int
    succeeded: int
    failed: int
    skipped: int
    results: list[FileResult]
    duration: float


# ===========================================================================
# BatchEngine
# ===========================================================================


class BatchEngine:
    """批量任务编排引擎。

    职责：
    - 扫描目录，收集待处理文件
    - 批量执行匹配 → 刮削 → 整理流程
    - 维护断点续传 checkpoint
    """

    def __init__(
        self,
        config: MediaLensConfig,
        db_path: Optional[str] = None,
    ) -> None:
        """初始化 BatchEngine。

        Args:
            config: 全局配置
            db_path: 可选的数据库路径（覆盖 config.db_path）
        """
        self.config = config
        actual_db_path = db_path or config.db_path
        self.db = DatabaseManager(db_path=actual_db_path)
        self.db.initialize()
        # 确保 checkpoint 表存在
        self.db.init_checkpoint_tables()

        # 延迟初始化（需要 tmdb_api_key 时创建）
        self._pipeline: Optional[MatcherPipeline] = None
        self._nfo: Optional[NfoGenerator] = None
        self._fm: Optional[FileManager] = None

        # 运行时状态
        self._cancelled = False

    # ------------------------------------------------------------------
    # 扫描目录
    # ------------------------------------------------------------------

    def scan_directory(
        self, path: str, recursive: bool = True,
    ) -> ScanResult:
        """扫描目录，返回结构化的文件列表。

        实现要点：
        - 调用 FormatDetector.get_video_files() + detect_format()
        - 查询数据库，过滤已匹配的文件
        - 按文件大小降序排列（大文件优先处理）

        Args:
            path: 待扫描的目录路径
            recursive: 是否递归子目录

        Returns:
            扫描结果
        """
        scan_path = Path(path)
        if not scan_path.exists():
            return ScanResult(total=0, new_files=[], existing_files=0, errors=[f"路径不存在: {path}"])

        errors: list[str] = []
        video_files: list[str] = []

        if scan_path.is_dir():
            try:
                video_files = get_video_files(path)
            except Exception as exc:
                errors.append(f"扫描目录失败: {exc}")
                return ScanResult(total=0, new_files=[], existing_files=0, errors=errors)
        else:
            # 单个文件
            video_files = [path]

        if not video_files:
            return ScanResult(total=0, new_files=[], existing_files=0, errors=errors)

        # 构建文件信息列表
        file_infos: list[FileInfo] = []
        for vf in video_files:
            try:
                vf_path = Path(vf)
                size = vf_path.stat().st_size
                mtime = vf_path.stat().st_mtime
                fmt = detect_format(vf)

                file_infos.append(FileInfo(
                    path=vf,
                    format=fmt,
                    size=size,
                    modified_time=mtime,
                ))
            except Exception as exc:
                errors.append(f"检测文件失败 {vf}: {exc}")

        if not file_infos:
            return ScanResult(total=0, new_files=[], existing_files=0, errors=errors)

        # 按文件大小降序排列
        file_infos.sort(key=lambda f: f.size, reverse=True)

        # 过滤已匹配的文件
        original_count = len(file_infos)
        new_files: list[FileInfo] = []
        existing_files = 0

        for fi in file_infos:
            match_row = self.db.get_match_by_path(fi.path)
            if match_row is None:
                new_files.append(fi)
            else:
                existing_files += 1

        return ScanResult(
            total=original_count,
            new_files=new_files,
            existing_files=existing_files,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # 批量处理
    # ------------------------------------------------------------------

    def run_batch(
        self,
        paths: list[str],
        options: Optional[BatchOptions] = None,
        job_id: Optional[str] = None,
    ) -> BatchResult:
        """执行批量处理任务。

        Args:
            paths: 待处理的文件路径列表
            options: 处理选项
            job_id: 可选的作业ID（用于断点续传）

        Returns:
            批量处理结果
        """
        opts = options or BatchOptions()
        actual_job_id = job_id or f"batch_{uuid.uuid4().hex[:12]}"
        self._cancelled = False

        # 初始化子模块
        pipeline = self._get_pipeline(opts)
        nfo = self._get_nfo() if opts.scrape else None
        fm = self._get_fm() if opts.organize else None

        # 创建任务记录到数据库
        opts_dict = asdict(opts)
        # 转换枚举为字符串
        self.db.create_job(actual_job_id, len(paths), opts_dict)

        # 记录 checkpoint（仅新任务写入 pending，续传时不覆盖已有记录）
        if not job_id:
            for p in paths:
                self.db.save_checkpoint(actual_job_id, p, "pending")

        start_time = time.time()
        results: list[FileResult] = []
        succeeded = 0
        failed = 0
        skipped = 0

        # 如果是断点续传，加载已完成列表
        done_paths: set[str] = set()
        if job_id:
            checkpoints = self.db.load_checkpoint(job_id)
            for cp in checkpoints:
                if cp["status"] == "done":
                    done_paths.add(cp["file_path"])

        # 并行处理（仅处理未完成文件）
        pending_paths = [p for p in paths if p not in done_paths]
        skipped = len(done_paths)

        # 单线程/多线程模式
        if opts.max_workers <= 1:
            for p in pending_paths:
                if self._cancelled:
                    break
                result = self._process_single_file(
                    p, opts, pipeline, nfo, fm, actual_job_id,
                )
                results.append(result)
                if result.error:
                    failed += 1
                else:
                    succeeded += 1
                # 更新任务进度
                self.db.update_job_status(
                    actual_job_id, "running", succeeded + failed,
                )
        else:
            with ThreadPoolExecutor(max_workers=opts.max_workers) as executor:
                future_map = {
                    executor.submit(
                        self._process_single_file,
                        p, opts, pipeline, nfo, fm, actual_job_id,
                    ): p for p in pending_paths
                }

                for future in as_completed(future_map):
                    if self._cancelled:
                        break
                    try:
                        result = future.result(timeout=opts.file_timeout)
                        results.append(result)
                        if result.error:
                            failed += 1
                        else:
                            succeeded += 1
                    except Exception as exc:
                        path = future_map[future]
                        results.append(FileResult(
                            path=path,
                            matched=False,
                            error=f"处理超时或异常: {exc}",
                        ))
                        failed += 1

                    # 更新任务进度
                    self.db.update_job_status(
                        actual_job_id, "running", succeeded + failed,
                    )

        duration = time.time() - start_time

        # 更新任务为完成状态
        final_status = "cancelled" if self._cancelled else "completed"
        self.db.update_job_status(actual_job_id, final_status, succeeded + failed)

        return BatchResult(
            total=len(paths),
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            results=results,
            duration=duration,
        )

    def run_batch_from_directory(
        self,
        path: str,
        options: Optional[BatchOptions] = None,
        job_id: Optional[str] = None,
    ) -> BatchResult:
        """扫描目录 + 批量处理的一步到位接口。

        1. 调用 scan_directory() 获取文件列表
        2. 调用 run_batch() 处理所有新文件
        3. 返回完整结果

        Args:
            path: 目录路径
            options: 处理选项
            job_id: 可选的作业ID（用于断点续传）

        Returns:
            批量处理结果
        """
        scan_result = self.scan_directory(path)
        if not scan_result.new_files:
            return BatchResult(
                total=0,
                succeeded=0,
                failed=0,
                skipped=0,
                results=[],
                duration=0.0,
            )

        file_paths = [f.path for f in scan_result.new_files]
        return self.run_batch(file_paths, options=options, job_id=job_id)

    # ------------------------------------------------------------------
    # 断点续传
    # ------------------------------------------------------------------

    def save_checkpoint(
        self, job_id: str, processed: list[str], state: dict[str, Any],
    ) -> bool:
        """保存处理进度到数据库。

        Args:
            job_id: 作业ID
            processed: 已处理的文件路径列表
            state: 额外状态（如当前已处理数、错误数等）

        Returns:
            是否保存成功
        """
        try:
            for file_path in processed:
                self.db.save_checkpoint(job_id, file_path, "done")
            return True
        except Exception as exc:
            logger.error("保存 checkpoint 失败: %s", exc, exc_info=True)
            return False

    def load_checkpoint(self, job_id: str) -> Optional[dict[str, Any]]:
        """加载之前的处理进度。

        Args:
            job_id: 作业ID

        Returns:
            包含已完成文件列表和状态的 dict，或 None（不存在）
        """
        try:
            checkpoints = self.db.load_checkpoint(job_id)
            if not checkpoints:
                return None

            done_files = [
                cp["file_path"] for cp in checkpoints
                if cp["status"] == "done"
            ]
            failed_files = [
                cp["file_path"] for cp in checkpoints
                if cp["status"] == "failed"
            ]

            return {
                "job_id": job_id,
                "done": done_files,
                "failed": failed_files,
                "total": len(done_files) + len(failed_files),
                "all": checkpoints,
            }
        except Exception as exc:
            logger.error("加载 checkpoint 失败: %s", exc, exc_info=True)
            return None

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有任务记录。"""
        return self.db.list_jobs()

    def cancel(self) -> None:
        """取消当前运行中的批量任务。"""
        self._cancelled = True

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_pipeline(self, opts: BatchOptions) -> MatcherPipeline:
        """创建（或复用）MatcherPipeline 实例。"""
        if self._pipeline is None:
            api_key = self.config.tmdb_api_key
            self._pipeline = MatcherPipeline(tmdb_api_key=api_key, config=self.config)
        return self._pipeline

    def _get_nfo(self) -> NfoGenerator:
        """创建（或复用）NfoGenerator 实例。"""
        if self._nfo is None:
            self._nfo = NfoGenerator(
                tmdb_api_key=self.config.tmdb_api_key,
                language=self.config.tmdb_language,
            )
        return self._nfo

    def _get_fm(self) -> FileManager:
        """创建（或复用）FileManager 实例。"""
        if self._fm is None:
            self._fm = FileManager()
        return self._fm

    def _process_single_file(
        self,
        file_path: str,
        opts: BatchOptions,
        pipeline: MatcherPipeline,
        nfo: Optional[NfoGenerator],
        fm: Optional[FileManager],
        job_id: str,
    ) -> FileResult:
        """处理单个文件：匹配 → 可选刮削 → 可选整理。

        Args:
            file_path: 文件路径
            opts: 处理选项
            pipeline: 匹配管道
            nfo: NFO 生成器（可选）
            fm: 文件管理器（可选）
            job_id: 作业ID

        Returns:
            单个文件的处理结果
        """
        # 标记为处理中
        self.db.save_checkpoint(job_id, file_path, "processing")

        try:
            # 1. 匹配
            match_result: MatchResult = pipeline.match(file_path)

            fr = FileResult(
                path=file_path,
                matched=match_result.matched,
                title=match_result.title if match_result.matched else "",
                year=match_result.year,
                confidence=match_result.confidence,
                source=match_result.source.value if match_result.matched else "",
            )

            if not match_result.matched:
                self.db.save_checkpoint(job_id, file_path, "done", json.dumps(asdict(fr)))
                return fr

            # 2. 刮削（可选）
            if opts.scrape and nfo and match_result.matched:
                try:
                    nfo_target = opts.target_dir or str(Path(file_path).parent)
                    nfo_dir = Path(nfo_target)
                    nfo_dir.mkdir(parents=True, exist_ok=True)

                    if match_result.media_type == MediaType.MOVIE:
                        nfo_path = nfo_dir / "movie.nfo"
                        nfo.generate_movie_nfo(match_result, str(nfo_path))
                    else:
                        nfo_path = nfo_dir / "tvshow.nfo"
                        nfo.generate_tv_nfo(match_result, str(nfo_path))
                        if match_result.season and match_result.episode:
                            ep_path = nfo_dir / f"{Path(file_path).stem}.nfo"
                            nfo.generate_episode_nfo(match_result, str(ep_path))

                    fr.nfo_generated = True
                except Exception as exc:
                    logger.warning("刮削失败 %s: %s", file_path, exc)
                    if not opts.continue_on_error:
                        raise

            # 3. 整理（可选）
            if opts.organize and fm and match_result.matched and opts.target_dir:
                try:
                    rename_config = RenameConfig()
                    rename_config.create_hardlink = True
                    rename_config.keep_original = True

                    op = fm.organize_file(
                        source=file_path,
                        target_dir=opts.target_dir,
                        result=match_result,
                        config=rename_config,
                        dry_run=opts.dry_run,
                    )
                    fr.organized = op.success
                except Exception as exc:
                    logger.warning("整理失败 %s: %s", file_path, exc)
                    if not opts.continue_on_error:
                        raise

            # 标记为完成
            self.db.save_checkpoint(job_id, file_path, "done", json.dumps(asdict(fr)))
            return fr

        except Exception as exc:
            logger.error("处理失败 %s: %s", file_path, exc, exc_info=True)
            fr = FileResult(
                path=file_path,
                matched=False,
                error=str(exc),
            )
            self.db.save_checkpoint(job_id, file_path, "failed", json.dumps(asdict(fr)))
            return fr
