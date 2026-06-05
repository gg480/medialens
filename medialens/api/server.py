"""
MediaLens FastAPI REST API 服务

提供与前端约定的所有 RESTful 接口，统一使用 /api 前缀，
所有响应格式统一为 {"success": true, "data": ...} 或 {"success": false, "error": "..."}。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from medialens.cli.config_loader import load_config
from medialens.detector.format_detector import (
    detect_format,
    get_video_files,
)
from medialens.filemgr.file_manager import FileManager
from medialens.matcher.matcher_pipeline import MatcherPipeline
from medialens.models import (
    FileFormat,
    MediaLensConfig,
    RenameConfig,
)
from medialens.orchestrator.batch_engine import BatchEngine, BatchOptions
from medialens.storage.database import DatabaseManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="MediaLens API",
    version="0.1.0",
    description="LLM 辅助的智能媒体刮削重命名工具 API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 全局运行时状态（在 lifespan 中初始化）
# ---------------------------------------------------------------------------

_config: Optional[MediaLensConfig] = None
_db: Optional[DatabaseManager] = None


# ---------------------------------------------------------------------------
# Pydantic 请求模型
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    path: str
    recursive: bool = True


class MatchRequest(BaseModel):
    path: str
    tmdb_key: Optional[str] = None
    language: str = "zh-CN"


class OrganizeRequest(BaseModel):
    path: str
    target_dir: str
    mode: str = "hardlink"
    dry_run: bool = True


class ConfigUpdate(BaseModel):
    tmdb_api_key: Optional[str] = None
    tmdb_language: Optional[str] = None
    llm_provider: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    organize_mode: Optional[str] = None
    dry_run: Optional[bool] = None


class BatchScanRequest(BaseModel):
    path: str
    recursive: bool = True


class BatchRunRequest(BaseModel):
    paths: list[str]
    scrape: bool = True
    organize: bool = False
    target_dir: str = ""
    dry_run: bool = True
    max_workers: int = 2
    continue_on_error: bool = True
    llm_enabled: bool = True
    job_id: Optional[str] = None


class BatchRunFromDirRequest(BaseModel):
    path: str
    scrape: bool = True
    organize: bool = False
    target_dir: str = ""
    dry_run: bool = True
    max_workers: int = 2
    continue_on_error: bool = True
    llm_enabled: bool = True
    job_id: Optional[str] = None


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _ensure_initialized() -> None:
    """确保运行时状态已初始化。"""
    if _config is None or _db is None:
        raise RuntimeError("服务未初始化，请先调用 /api/config 或重启服务")


def _get_db() -> DatabaseManager:
    """获取数据库管理器实例（延迟初始化）。"""
    global _db
    if _db is None:
        cfg = _get_config()
        _db = DatabaseManager(db_path=cfg.db_path)
        _db.initialize()
    return _db


def _get_config() -> MediaLensConfig:
    """获取当前配置（延迟加载）。"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _format_size(size_bytes: int) -> str:
    """将字节数转为人类可读的大小字符串。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ---------------------------------------------------------------------------
# 响应工具
# ---------------------------------------------------------------------------


def _ok(data: Any = None) -> dict[str, Any]:
    """构建成功响应。"""
    return {"success": True, "data": data}


def _err(message: str, status_code: int = 500) -> dict[str, Any]:
    """构建错误响应。"""
    raise HTTPException(status_code=status_code, detail={"success": False, "error": message})


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------


@app.get("/api/stats")
async def get_stats():
    """获取仪表盘统计数据。"""
    try:
        db = _get_db()
        stats = db.get_stats()
        return _ok(stats)
    except Exception as exc:
        logger.error("获取统计数据失败: %s", exc, exc_info=True)
        return _err(f"获取统计数据失败: {exc}")


@app.get("/api/matches/recent")
async def get_recent_matches(limit: int = Query(10, ge=1, le=100)):
    """获取最近的匹配记录。"""
    try:
        db = _get_db()
        rows = db.get_recent_matches(limit=limit)

        # 格式化为前端期望的格式
        items = []
        for row in rows:
            items.append({
                "id": row["id"],
                "file": row["file_path"],
                "title": row["matched_title"] or "",
                "original_title": "",
                "year": row["matched_year"],
                "tmdb_id": row["tmdb_id"],
                "confidence": row["confidence"],
                "source": row["match_source"],
                "format": row["file_format"],
                "overview": row.get("overview", ""),
                "poster": row.get("poster_path"),
                "season": row.get("parsed_season"),
                "episode": row.get("parsed_episode"),
                "created_at": row["created_at"],
            })

        return _ok(items)
    except Exception as exc:
        logger.error("获取最近匹配失败: %s", exc, exc_info=True)
        return _err(f"获取最近匹配失败: {exc}")


@app.get("/api/files/pending")
async def get_pending_files():
    """获取待处理文件列表。"""
    try:
        cfg = _get_config()
        # 使用 data_dir 作为默认扫描目录
        scan_dir = cfg.data_dir

        if not scan_dir or not Path(scan_dir).exists():
            # BONUS: 未配置媒体目录时返回空列表
            return _ok([])

        db = _get_db()

        # 在异步上下文中同步执行文件扫描
        all_files = await asyncio.to_thread(get_video_files, scan_dir)

        # 过滤已匹配的文件
        pending = db.get_pending_files(all_files)

        # 格式化返回
        items = []
        for file_path in pending:
            p = Path(file_path)
            fmt = await asyncio.to_thread(detect_format, file_path)
            # 计算大小
            try:
                size = p.stat().st_size
            except OSError:
                size = 0

            items.append({
                "name": str(p),
                "format": fmt.value,
                "size": _format_size(size),
                "status": "pending",
            })

        return _ok(items)
    except Exception as exc:
        logger.error("获取待处理文件失败: %s", exc, exc_info=True)
        return _ok([])  # 任何错误都返回空列表，不阻塞前端


@app.get("/api/logs")
async def get_logs(
    level: str = Query("all", pattern="^(all|info|success|warn|error)$"),
    limit: int = Query(50, ge=1, le=200),
):
    """获取活动日志。"""
    try:
        db = _get_db()
        logs = db.get_logs(level=level, limit=limit)
        return _ok(logs)
    except Exception as exc:
        logger.error("获取日志失败: %s", exc, exc_info=True)
        return _ok([])


@app.get("/api/config")
async def get_config():
    """获取当前配置。"""
    try:
        cfg = _get_config()

        # 格式化为前端期望的结构
        config_data = {
            "tmdb": {
                "api_key": cfg.tmdb_api_key or "",
                "language": cfg.tmdb_language or "zh-CN",
                "confidence_threshold": cfg.tmdb_confidence_threshold,
            },
            "llm": {
                "provider": cfg.llm_provider or "openai",
                "model": cfg.llm_model or "gpt-4o-mini",
                "api_key": cfg.llm_api_key or "",
                "api_base": cfg.llm_api_base or "",
                "confidence_threshold": cfg.llm_confidence_threshold,
            },
            "organize": {
                "mode": cfg.organize_mode or "hardlink",
                "movie_template": cfg.rename.movie_template,
                "tv_template": cfg.rename.tv_template,
                "strategy": cfg.rename.organize_by,
                "dry_run": cfg.dry_run,
            },
            "db": {
                "path": cfg.db_path,
                "data_dir": cfg.data_dir,
            },
        }

        return _ok(config_data)
    except Exception as exc:
        logger.error("获取配置失败: %s", exc, exc_info=True)
        return _err(f"获取配置失败: {exc}")


@app.post("/api/config")
async def save_config(update: ConfigUpdate):
    """更新配置。"""
    try:
        global _config
        cfg = _get_config()

        # 更新运行时配置
        if update.tmdb_api_key is not None:
            cfg.tmdb_api_key = update.tmdb_api_key
        if update.tmdb_language is not None:
            cfg.tmdb_language = update.tmdb_language
        if update.llm_provider is not None:
            cfg.llm_provider = update.llm_provider
        if update.llm_api_key is not None:
            cfg.llm_api_key = update.llm_api_key
        if update.llm_model is not None:
            cfg.llm_model = update.llm_model
        if update.organize_mode is not None:
            cfg.organize_mode = update.organize_mode
        if update.dry_run is not None:
            cfg.dry_run = update.dry_run

        _config = cfg

        # 返回更新后的完整配置
        return await get_config()
    except Exception as exc:
        logger.error("保存配置失败: %s", exc, exc_info=True)
        return _err(f"保存配置失败: {exc}")


@app.post("/api/scan")
async def scan_directory(req: ScanRequest):
    """扫描目录，返回未匹配的文件列表。"""
    try:
        scan_path = req.path
        if not Path(scan_path).exists():
            raise HTTPException(status_code=404, detail={"success": False, "error": f"目录不存在: {scan_path}"})

        db = _get_db()

        # 扫描视频文件
        all_files = await asyncio.to_thread(get_video_files, scan_path)

        # 过滤已匹配的文件
        pending = db.get_pending_files(all_files)

        items = []
        for file_path in pending:
            p = Path(file_path)
            fmt = await asyncio.to_thread(detect_format, file_path)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0

            items.append({
                "name": str(p),
                "format": fmt.value,
                "size": _format_size(size),
                "status": "pending",
            })

        return _ok({"path": scan_path, "files": items})
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("扫描目录失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"success": False, "error": f"扫描目录失败: {exc}"})


@app.post("/api/match")
async def match_file(req: MatchRequest):
    """匹配单个文件。"""
    try:
        file_path = req.path
        if not Path(file_path).exists():
            raise HTTPException(status_code=404, detail={"success": False, "error": f"文件不存在: {file_path}"})

        cfg = _get_config()

        # 允许请求中覆盖 TMDB Key
        tmdb_key = req.tmdb_key or cfg.tmdb_api_key
        if not tmdb_key:
            raise HTTPException(status_code=400, detail={"success": False, "error": "TMDB API Key 未配置"})

        # 在异步上下文中同步执行匹配
        pipeline = MatcherPipeline(tmdb_api_key=tmdb_key, config=cfg)

        result = await asyncio.to_thread(pipeline.match, file_path)

        response_data = {
            "matched": result.matched,
            "media_type": result.media_type.value,
            "tmdb_id": result.tmdb_id,
            "title": result.title,
            "original_title": result.original_title,
            "year": result.year,
            "source": result.source.value,
            "confidence": result.confidence,
            "overview": result.overview,
            "poster_path": result.poster_path,
            "backdrop_path": result.backdrop_path,
            "season": result.season,
            "episode": result.episode,
            "candidates": [
                {
                    "tmdb_id": c.tmdb_id,
                    "title": c.title,
                    "original_title": c.original_title,
                    "year": c.year,
                    "media_type": c.media_type.value,
                    "match_score": c.match_score,
                }
                for c in result.candidates
            ],
        }

        return _ok(response_data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("匹配文件失败: %s", exc, exc_info=True)
        return _err(f"匹配文件失败: {exc}")


@app.post("/api/organize")
async def organize_file(req: OrganizeRequest):
    """整理已匹配的文件。

    流程：
    1. 先匹配文件（如果尚未匹配）
    2. 再执行文件整理操作
    """
    try:
        file_path = req.path
        if not Path(file_path).exists():
            raise HTTPException(status_code=404, detail={"success": False, "error": f"文件不存在: {file_path}"})

        cfg = _get_config()
        db = _get_db()

        # 1. 先匹配
        pipeline = MatcherPipeline(tmdb_api_key=cfg.tmdb_api_key, config=cfg)
        match_result = await asyncio.to_thread(pipeline.match, file_path)

        if not match_result.matched:
            raise HTTPException(status_code=400, detail={"success": False, "error": f"无法匹配文件: {file_path}"})

        # 2. 执行文件整理
        fm = FileManager()
        rename_cfg = RenameConfig(
            create_hardlink=(req.mode == "hardlink"),
            keep_original=(req.mode == "copy") or (req.mode == "hardlink"),
        )

        operation = await asyncio.to_thread(
            fm.organize_file,
            source=file_path,
            target_dir=req.target_dir,
            result=match_result,
            config=rename_cfg,
            dry_run=req.dry_run,
        )

        # 3. 记录操作日志
        db.log_file_operation(
            source=str(operation.source_path),
            target=str(operation.target_path),
            operation=operation.operation,
            dry_run=operation.dry_run,
            success=operation.success,
            error=operation.error or "",
        )

        response_data = {
            "source_path": str(operation.source_path),
            "target_path": str(operation.target_path),
            "operation": operation.operation,
            "dry_run": operation.dry_run,
            "success": operation.success,
            "error": operation.error,
        }

        return _ok(response_data)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("整理文件失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"success": False, "error": f"整理文件失败: {exc}"})

# ---------------------------------------------------------------------------
# 批量处理端点
# ---------------------------------------------------------------------------


@app.post("/api/batch/scan")
async def batch_scan(req: BatchScanRequest):
    """扫描目录，返回结构化文件列表。"""
    try:
        if not Path(req.path).exists():
            raise HTTPException(status_code=404, detail={"success": False, "error": f"路径不存在: {req.path}"})

        cfg = _get_config()
        engine = BatchEngine(config=cfg)
        scan_result = await asyncio.to_thread(engine.scan_directory, req.path)

        return _ok({
            "total": scan_result.total,
            "new_files": [
                {
                    "path": fi.path,
                    "format": fi.format.value,
                    "size": fi.size,
                    "size_display": _format_size(fi.size),
                    "modified_time": fi.modified_time,
                }
                for fi in scan_result.new_files
            ],
            "existing_files": scan_result.existing_files,
            "errors": scan_result.errors,
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("批量扫描失败: %s", exc, exc_info=True)
        return _err(f"批量扫描失败: {exc}")


@app.post("/api/batch/run")
async def batch_run(req: BatchRunRequest):
    """批量处理文件。"""
    try:
        cfg = _get_config()

        # 验证文件存在
        for p in req.paths:
            if not Path(p).exists():
                raise HTTPException(status_code=404, detail={"success": False, "error": f"文件不存在: {p}"})

        # 检查 TMDB Key
        if not cfg.tmdb_api_key:
            raise HTTPException(status_code=400, detail={"success": False, "error": "TMDB API Key 未配置"})

        opts = BatchOptions(
            scrape=req.scrape,
            organize=req.organize,
            target_dir=req.target_dir,
            dry_run=req.dry_run,
            max_workers=req.max_workers,
            continue_on_error=req.continue_on_error,
            llm_enabled=req.llm_enabled,
        )

        engine = BatchEngine(config=cfg)
        batch_result = await asyncio.to_thread(
            engine.run_batch, req.paths, opts, req.job_id,
        )

        return _ok({
            "total": batch_result.total,
            "succeeded": batch_result.succeeded,
            "failed": batch_result.failed,
            "skipped": batch_result.skipped,
            "duration": batch_result.duration,
            "results": [
                {
                    "path": r.path,
                    "matched": r.matched,
                    "title": r.title,
                    "year": r.year,
                    "confidence": r.confidence,
                    "source": r.source,
                    "nfo_generated": r.nfo_generated,
                    "organized": r.organized,
                    "error": r.error,
                }
                for r in batch_result.results
            ],
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("批量处理失败: %s", exc, exc_info=True)
        return _err(f"批量处理失败: {exc}")


@app.post("/api/batch/run-from-dir")
async def batch_run_from_dir(req: BatchRunFromDirRequest):
    """扫描目录并批量处理。"""
    try:
        cfg = _get_config()

        if not Path(req.path).exists():
            raise HTTPException(status_code=404, detail={"success": False, "error": f"路径不存在: {req.path}"})

        if not cfg.tmdb_api_key:
            raise HTTPException(status_code=400, detail={"success": False, "error": "TMDB API Key 未配置"})

        opts = BatchOptions(
            scrape=req.scrape,
            organize=req.organize,
            target_dir=req.target_dir,
            dry_run=req.dry_run,
            max_workers=req.max_workers,
            continue_on_error=req.continue_on_error,
            llm_enabled=req.llm_enabled,
        )

        engine = BatchEngine(config=cfg)
        batch_result = await asyncio.to_thread(
            engine.run_batch_from_directory, req.path, opts, req.job_id,
        )

        return _ok({
            "total": batch_result.total,
            "succeeded": batch_result.succeeded,
            "failed": batch_result.failed,
            "skipped": batch_result.skipped,
            "duration": batch_result.duration,
            "results": [
                {
                    "path": r.path,
                    "matched": r.matched,
                    "title": r.title,
                    "year": r.year,
                    "confidence": r.confidence,
                    "source": r.source,
                    "nfo_generated": r.nfo_generated,
                    "organized": r.organized,
                    "error": r.error,
                }
                for r in batch_result.results
            ],
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("目录批量处理失败: %s", exc, exc_info=True)
        return _err(f"目录批量处理失败: {exc}")


@app.get("/api/batch/jobs")
async def batch_list_jobs():
    """列出所有批量任务记录。"""
    try:
        cfg = _get_config()
        engine = BatchEngine(config=cfg)
        jobs = engine.list_jobs()
        return _ok(jobs)
    except Exception as exc:
        logger.error("获取任务列表失败: %s", exc, exc_info=True)
        return _ok([])


@app.post("/api/batch/resume/{job_id}")
async def batch_resume(job_id: str):
    """断点续传批量任务。"""
    try:
        cfg = _get_config()
        engine = BatchEngine(config=cfg)

        checkpoint = await asyncio.to_thread(engine.load_checkpoint, job_id)
        if checkpoint is None:
            raise HTTPException(status_code=404, detail={"success": False, "error": f"任务不存在: {job_id}"})

        # 收集未完成的文件
        pending_files: list[str] = []
        for cp in checkpoint["all"]:
            if cp["status"] in ("failed", "pending", "processing"):
                pending_files.append(cp["file_path"])

        if not pending_files:
            return _ok({
                "job_id": job_id,
                "message": "所有文件已处理完成",
                "total": 0,
                "succeeded": 0,
                "failed": 0,
            })

        opts = BatchOptions(
            max_workers=2,
            continue_on_error=True,
        )

        batch_result = await asyncio.to_thread(
            engine.run_batch, pending_files, opts, job_id,
        )

        return _ok({
            "job_id": job_id,
            "total": batch_result.total,
            "succeeded": batch_result.succeeded,
            "failed": batch_result.failed,
            "duration": batch_result.duration,
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("续传任务失败: %s", exc, exc_info=True)
        return _err(f"续传任务失败: {exc}")


# ---------------------------------------------------------------------------
# 静态文件服务（前端 SPA）
# ---------------------------------------------------------------------------

import os
_web_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")
