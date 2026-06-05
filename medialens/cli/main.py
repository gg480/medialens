"""
MediaLens CLI 命令行入口

基于 click 框架提供完整的命令行界面，
使用 rich 实现美观的控制台输出。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from medialens import __version__
from medialens.filemgr.file_manager import DirectoryOrganizer, FileManager
from medialens.matcher.matcher_pipeline import MatcherPipeline
from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaLensConfig,
    MediaType,
    RenameConfig,
)
from medialens.orchestrator.batch_engine import BatchEngine, BatchOptions, BatchResult
from medialens.scraper.nfo_generator import NfoGenerator
from medialens.storage.database import DatabaseManager

from .config_loader import generate_default_config, load_config

# ---------------------------------------------------------------------------
# 日志与控制台
# ---------------------------------------------------------------------------

console = Console()

_log_levels: dict[int, int] = {
    0: logging.WARNING,
    1: logging.INFO,
    2: logging.DEBUG,
}


def _setup_logging(verbose: int) -> None:
    """配置日志级别。

    Args:
        verbose: 冗余度（0=WARNING, 1=INFO, 2=DEBUG）
    """
    level = _log_levels.get(verbose, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _format_size(size_bytes: int) -> str:
    """将字节数转为人类可读的大小字符串。"""
    if size_bytes <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _create_pipeline(
    config: MediaLensConfig,
    tmdb_key: Optional[str] = None,
    no_cache: bool = False,
) -> MatcherPipeline:
    """创建 MatcherPipeline 实例。

    使用命令行传入的 tmdb_key 优先，其次从 config 读取。

    Args:
        config: 媒体配置
        tmdb_key: 可选的 TMDB API Key
        no_cache: 是否禁用缓存

    Returns:
        配置好的 MatcherPipeline 实例
    """
    api_key = tmdb_key or config.tmdb_api_key
    if not api_key:
        console.print(
            "[red]错误:[/] TMDB API Key 未配置。请通过 --tmdb-key 传入或设置环境变量 TMDB_API_KEY。"
        )
        sys.exit(1)

    pipeline = MatcherPipeline(tmdb_api_key=api_key, config=config)

    # 确保数据库已初始化
    pipeline.db.initialize()

    return pipeline


def _build_result_table(results: list[tuple[str, MatchResult, str, str]]) -> Table:
    """构建匹配结果表格。

    Args:
        results: (路径, 匹配结果, 刮削状态, 整理状态) 元组列表

    Returns:
        格式化后的 rich Table
    """
    table = Table(
        title="MediaLens - 匹配结果",
        show_header=True,
        header_style="bold cyan",
        border_style="blue",
    )
    table.add_column("文件名", style="white", no_wrap=True)
    table.add_column("匹配结果", width=12)
    table.add_column("标题", style="green")
    table.add_column("年份", width=6)
    table.add_column("TMDB ID", width=10)
    table.add_column("置信度", width=8)
    table.add_column("刮削状态", width=14)
    table.add_column("整理状态", width=18)

    for path, result, scrape_status, organize_status in results:
        filename = Path(path).name
        if result.matched:
            match_text = "[green]成功匹配[/]"
            title_text = result.display_name
            year_text = str(result.year) if result.year else "-"
            tmdb_text = str(result.tmdb_id)
            confidence_text = f"{result.confidence:.1f} ({result.source.value})"
        else:
            match_text = "[red]未匹配[/]"
            title_text = "[dim]N/A[/]"
            year_text = "-"
            tmdb_text = "-"
            confidence_text = "-"

        table.add_row(
            filename,
            match_text,
            title_text,
            year_text,
            tmdb_text,
            confidence_text,
            scrape_status,
            organize_status,
        )

    return table


def _display_result_detail(
    path: str,
    result: MatchResult,
    scrape_status: str = "",
    organize_status: str = "",
) -> None:
    """在 Panel 中显示单个匹配结果的详细信息。

    Args:
        path: 文件路径
        result: 匹配结果
        scrape_status: 刮削状态文本
        organize_status: 整理状态文本
    """
    filename = Path(path).name
    matched_icon = "\u2705" if result.matched else "\u274c"
    match_text = f"{matched_icon} {'成功匹配' if result.matched else '未匹配'}"

    info_lines = [
        f"[bold]文件名:[/] {filename}",
        f"[bold]匹配结果:[/] {match_text}",
    ]

    if result.matched:
        info_lines.append(f"[bold]标题:[/] {result.display_name}")
        info_lines.append(f"[bold]年份:[/] {result.year or '-'}")
        info_lines.append(f"[bold]TMDB ID:[/] {result.tmdb_id}")
        info_lines.append(f"[bold]置信度:[/] {result.confidence:.1f} ({result.source.value})")
        if result.overview:
            info_lines.append(f"[bold]简介:[/] {result.overview[:100]}..." if len(result.overview) > 100 else f"[bold]简介:[/] {result.overview}")

    if scrape_status:
        info_lines.append(f"[bold]刮削状态:[/] {scrape_status}")
    if organize_status:
        info_lines.append(f"[bold]整理状态:[/] {organize_status}")

    panel = Panel(
        "\n".join(info_lines),
        title=f"[bold]{filename}[/]",
        border_style="green" if result.matched else "red",
        width=100,
    )
    console.print(panel)
    console.print()


def _do_scrape(
    nfo: NfoGenerator,
    result: MatchResult,
    target_dir: str,
    download_images: bool = False,
    overwrite: bool = False,
) -> str:
    """对已匹配的文件执行 NFO 刮削。

    Args:
        nfo: NfoGenerator 实例
        result: 匹配结果
        target_dir: 输出目录
        download_images: 是否下载图片
        overwrite: 是否覆盖已存在的 NFO

    Returns:
        状态文本
    """
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    # 生成 NFO 文件路径
    if result.media_type.value == "tv":
        if result.season is not None:
            nfo_path = target / "season.nfo"
        else:
            nfo_path = target / "tvshow.nfo"
    else:
        nfo_path = target / "movie.nfo"

    # 检查是否已存在
    if nfo_path.exists() and not overwrite:
        return "[yellow]NFO 已存在[/]"

    try:
        if result.media_type.value == "movie":
            nfo.generate_movie_nfo(result, str(nfo_path))
        else:
            nfo.generate_tv_nfo(result, str(nfo_path))

        # 如果匹配结果中含季/集信息，生成 episode NFO
        if result.media_type.value == "tv" and result.season and result.episode:
            ep_nfo_path = target / f"{Path(nfo_path).stem}.episode.nfo"
            nfo.generate_episode_nfo(result, str(ep_nfo_path))
    except Exception as exc:
        return f"[red]NFO 生成失败: {exc}[/]"

    status = "[green]NFO 已生成[/]"

    if download_images:
        try:
            nfo.download_images(result, str(target))
            status += " + [green]图片已下载[/]"
        except Exception as exc:
            status += f" + [red]图片下载失败: {exc}[/]"

    return status


def _do_organize(
    fm: FileManager,
    source: str,
    target_dir: str,
    result: MatchResult,
    dry_run: bool,
    mode: str,
) -> str:
    """对已匹配的文件执行整理操作。

    Args:
        fm: FileManager 实例
        source: 源文件路径
        target_dir: 目标目录
        result: 匹配结果
        dry_run: 是否仅预览
        mode: 整理模式（hardlink/copy/move）

    Returns:
        状态文本
    """
    rename_config = RenameConfig()
    rename_config.create_hardlink = mode == "hardlink"
    rename_config.keep_original = mode != "move"

    op = fm.organize_file(
        source=source,
        target_dir=target_dir,
        result=result,
        config=rename_config,
        dry_run=dry_run,
    )

    if dry_run:
        if op.success:
            return f"\U0001f537 预览: {op.target_path.name}"
        return f"[red]预览失败: {op.error}[/]"

    if op.success:
        return f"[green]\u2705 {op.operation}: {op.target_path}[/]"
    return f"[red]{op.operation} 失败: {op.error}[/]"


# ===========================================================================
# CLI 主入口
# ===========================================================================


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--config", "-c", default="config.yaml", help="配置文件路径（默认 config.yaml）", show_default=True)
@click.option("--db", default="data/medialens.db", help="数据库路径（默认 data/medialens.db）", show_default=True)
@click.option("--dry-run", is_flag=True, default=False, help="预览模式（不执行实际操作）")
@click.option("--verbose", "-v", count=True, help="详细输出（-v INFO, -vv DEBUG）")
@click.version_option(version=__version__, prog_name="medialens")
@click.pass_context
def cli(
    ctx: click.Context,
    config: str,
    db: str,
    dry_run: bool,
    verbose: int,
) -> None:
    """MediaLens - LLM 辅助的智能媒体刮削重命名工具

    支持自动识别媒体文件格式，从 TMDB 获取元数据，
    生成 Kodi/Emby/Jellyfin 兼容的 NFO 文件，并整理媒体库。
    """
    _setup_logging(verbose)
    ctx.ensure_object(dict)

    # 加载配置
    overrides: dict[str, Any] = {}
    if db:
        overrides["db_path"] = db
    if dry_run:
        overrides["dry_run"] = True

    try:
        cfg = load_config(config_path=config, overrides=overrides)
    except Exception as exc:
        console.print(f"[red]配置加载失败:[/] {exc}")
        sys.exit(1)

    ctx.obj["config"] = cfg
    ctx.obj["config_path"] = config
    ctx.obj["db_path"] = db
    ctx.obj["dry_run"] = dry_run


# ===========================================================================
# match 命令
# ===========================================================================


@cli.command()
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--tmdb-key", help="TMDB API Key（优先级高于配置文件和环境变量）")
@click.option("--language", default=None, help="TMDB 语言（默认 zh-CN）")
@click.option("--llm-provider", default=None, help="LLM provider（openai/anthropic/ollama）")
@click.option("--llm-key", "--llm-api-key", default=None, help="LLM API Key")
@click.option("--llm-model", default=None, help="LLM 模型名")
@click.option("--output", "-o", default=None, type=click.Path(), help="输出目录（整理后的文件存放位置）")
@click.option("--scrape/--no-scrape", default=False, help="匹配后自动刮削 NFO")
@click.option("--organize/--no-organize", default=False, help="匹配后自动整理文件")
@click.option("--format", "file_format", default=None, type=click.Choice(["auto", "bdmv", "iso", "episode"]), help="指定文件格式（默认 auto）")
@click.option("--no-cache", is_flag=True, default=False, help="跳过缓存")
@click.option("--images", is_flag=True, default=False, help="同时下载图片（需配合 --scrape）")
@click.pass_context
def match(
    ctx: click.Context,
    paths: tuple[str, ...],
    tmdb_key: Optional[str],
    language: Optional[str],
    llm_provider: Optional[str],
    llm_key: Optional[str],
    llm_model: Optional[str],
    output: Optional[str],
    scrape: bool,
    organize: bool,
    file_format: Optional[str],
    no_cache: bool,
    images: bool,
) -> None:
    """匹配单个或多个文件（核心流程）

    PATH 可以是单个文件、目录（BDMV 原盘结构）或多个路径。
    按 Cache → TMDB 精确 → TMDB 模糊 → LLM 补充 的优先级链执行匹配。
    """
    config: MediaLensConfig = ctx.obj["config"]
    dry_run = ctx.obj.get("dry_run", False)

    # 合并命令行覆盖到 config
    if language:
        config.tmdb_language = language
    if llm_provider:
        config.llm_provider = llm_provider
    if llm_key:
        config.llm_api_key = llm_key
    if llm_model:
        config.llm_model = llm_model
    if no_cache:
        pass  # MatcherPipeline 默认使用缓存

    # 创建管道
    pipeline = _create_pipeline(config, tmdb_key=tmdb_key, no_cache=no_cache)

    # 初始化 NFO 生成器和 FileManager（如果启用）
    nfo: Optional[NfoGenerator] = None
    if scrape:
        api_key = tmdb_key or config.tmdb_api_key
        nfo = NfoGenerator(tmdb_api_key=api_key, language=config.tmdb_language)

    fm = FileManager() if organize else None

    # 进度条
    path_list = list(paths)
    results_data: list[tuple[str, MatchResult, str, str]] = []
    default_media_type = MediaType.MOVIE

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )

    with progress:
        task = progress.add_task("[cyan]匹配中...", total=len(path_list))

        for p in path_list:
            progress.update(task, description=f"[cyan]匹配: {Path(p).name}")

            try:
                result: MatchResult = pipeline.match(p)
            except Exception as exc:
                console.print(f"\n[red]匹配失败 {p}: {exc}[/]")
                result = MatchResult(
                    matched=False,
                    media_type=default_media_type,
                    tmdb_id=0,
                    title=Path(p).name,
                    original_title="",
                    source=MatchSource.TMDB_FUZZY,
                    confidence=0.0,
                )

            scrape_status = ""
            organize_status = ""

            if scrape and result.matched and nfo:
                # 确定 NFO 输出目录
                nfo_target = output or str(Path(p).parent)
                scrape_status = _do_scrape(nfo, result, nfo_target, download_images=images)

            if organize and result.matched and fm:
                target = output or str(Path(p).parent)
                mode = config.organize_mode
                organize_status = _do_organize(fm, p, target, result, dry_run, mode)

            results_data.append((p, result, scrape_status, organize_status))
            progress.advance(task)

    # 输出结果
    console.print()
    if len(path_list) == 1:
        _display_result_detail(*results_data[0])
    else:
        table = _build_result_table(results_data)
        console.print(table)

    # 汇总
    matched_count = sum(1 for _, r, _, _ in results_data if r.matched)
    console.print(
        f"\n[bold]汇总:[/] {matched_count}/{len(path_list)} 匹配成功"
        f"  |  {'[green]✅' if scrape else '[dim]刮削未启用'}"
        f"  |  {'[green]✅' if organize else '[dim]整理未启用'}"
    )


# ===========================================================================
# batch 命令组（新）
# ===========================================================================


@cli.command("batch-scan")
@click.argument("path", required=True, type=click.Path(exists=True))
@click.option("--recursive/--no-recursive", "-r/-nr", default=True, show_default=True, help="递归子目录")
@click.pass_context
def batch_scan(ctx: click.Context, path: str, recursive: bool) -> None:
    """扫描目录，显示待处理的视频文件列表。"""
    config: MediaLensConfig = ctx.obj["config"]
    engine = BatchEngine(config=config)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("[cyan]扫描中...", total=None)
        scan_result = engine.scan_directory(path)

    if scan_result.errors:
        for err in scan_result.errors:
            console.print(f"[red]扫描错误:[/] {err}")

    if not scan_result.new_files:
        console.print("[yellow]未发现新的待处理文件[/]")
        return

    # 展示文件列表
    table = Table(title=f"扫描结果: {path}", border_style="blue")
    table.add_column("文件名", style="white", no_wrap=True)
    table.add_column("格式", width=14)
    table.add_column("大小", width=10)
    table.add_column("修改时间", width=20)

    for fi in scan_result.new_files[:20]:
        p = Path(fi.path)
        size_str = _format_size(fi.size)
        mtime_str = datetime.fromtimestamp(fi.modified_time).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(p.name, fi.format.value, size_str, mtime_str)

    console.print(table)

    if len(scan_result.new_files) > 20:
        console.print(f"[dim]...还有 {len(scan_result.new_files) - 20} 个文件未显示[/]")

    console.print(
        f"\n[bold]汇总:[/] 共 {scan_result.total} 个文件, "
        f"[green]新文件 {len(scan_result.new_files)}[/], "
        f"[dim]已匹配 {scan_result.existing_files}[/]"
    )


@cli.command("batch-run")
@click.argument("path", required=True, type=click.Path(exists=True))
@click.option("--scrape/--no-scrape", default=True, help="是否刮削 NFO")
@click.option("--organize", default=None, type=click.Path(), help="整理目标目录")
@click.option("--mode", default="hardlink", type=click.Choice(["hardlink", "copy", "move"]), help="整理模式")
@click.option("--dry-run/--no-dry-run", default=True, help="预览模式")
@click.option("--workers", default=2, type=int, help="并行工作数（默认2）")
@click.option("--no-llm", is_flag=True, default=False, help="禁用 LLM 补充")
@click.option("--tmdb-key", help="TMDB API Key")
@click.option("--language", default=None, help="TMDB 语言")
@click.pass_context
def batch_run(
    ctx: click.Context,
    path: str,
    scrape: bool,
    organize: Optional[str],
    mode: str,
    dry_run: bool,
    workers: int,
    no_llm: bool,
    tmdb_key: Optional[str],
    language: Optional[str],
) -> None:
    """批量处理视频文件（扫描 + 匹配 + 可选刮削 + 可选整理）。

    PATH 可以是目录（自动扫描）或单个文件。

    处理流程:
    1. 扫描目录，收集所有新视频文件
    2. 并行匹配（使用 MatcherPipeline）
    3. 可选刮削 NFO
    4. 可选整理文件到目标目录
    """
    config: MediaLensConfig = ctx.obj["config"]

    # 覆盖配置
    if tmdb_key:
        config.tmdb_api_key = tmdb_key
    if language:
        config.tmdb_language = language
    if no_llm:
        config.llm_provider = "none"
        config.enable_llm = False

    # 检查 TMDB Key
    if not config.tmdb_api_key:
        console.print("[red]错误:[/] TMDB API Key 未配置。请通过 --tmdb-key 传入或设置配置文件。")
        sys.exit(1)

    # 构造处理选项
    opts = BatchOptions(
        scrape=scrape,
        organize=bool(organize),
        target_dir=organize or "",
        dry_run=dry_run,
        max_workers=workers,
        continue_on_error=True,
        llm_enabled=not no_llm,
    )

    engine = BatchEngine(config=config)

    # 第一阶段：扫描
    console.print(f"[bold]扫描目录:[/] {path}")
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("[cyan]扫描中...", total=None)
        scan_result = engine.scan_directory(path)

    new_files = scan_result.new_files

    if not new_files:
        console.print("[yellow]未发现新的待处理文件[/]")
        return

    file_paths = [f.path for f in new_files]
    console.print(f"发现 [bold]{len(file_paths)}[/] 个待处理文件")

    # 第二阶段：批量处理
    console.print(f"[bold]批量处理启动:[/] workers={workers}, scrape={'是' if scrape else '否'}, organize={'是' if organize else '否'}")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    )

    batch_result: Optional[BatchResult] = None
    with progress:
        task = progress.add_task("[cyan]处理中...", total=len(file_paths))

        # 使用内部回调更新进度条
        original_count = [0]

        def _update_progress():
            original_count[0] += 1
            progress.update(task, completed=original_count[0])

        # 用单线程模式但通过 rich 展示进度
        opts.max_workers = workers
        batch_result = engine.run_batch(file_paths, options=opts)
        progress.update(task, completed=len(file_paths))

    if batch_result is None:
        return

    # 输出结果
    console.print()
    result_table = Table(title="批量处理结果", border_style="blue")
    result_table.add_column("文件名", style="white", no_wrap=True)
    result_table.add_column("匹配", width=6)
    result_table.add_column("标题", style="green")
    result_table.add_column("年份", width=6)
    result_table.add_column("置信度", width=8)
    result_table.add_column("刮削", width=6)
    result_table.add_column("整理", width=6)
    result_table.add_column("错误")

    for fr in batch_result.results:
        matched_text = "[green]是[/]" if fr.matched else "[red]否[/]"
        nfo_text = "[green]是[/]" if fr.nfo_generated else "[dim]-[/]"
        org_text = "[green]是[/]" if fr.organized else "[dim]-[/]"
        error_text = f"[red]{fr.error}[/]" if fr.error else "[dim]-[/]"
        title_text = fr.title if fr.title else "[dim]N/A[/]"
        year_text = str(fr.year) if fr.year else "-"
        confidence_text = f"{fr.confidence:.1f}" if fr.confidence else "-"

        p = Path(fr.path)
        result_table.add_row(
            p.name, matched_text, title_text, year_text,
            confidence_text, nfo_text, org_text, error_text,
        )

    console.print(result_table)

    console.print(
        f"\n[bold]汇总:[/] 共 {batch_result.total} 个, "
        f"[green]成功 {batch_result.succeeded}[/], "
        f"[red]失败 {batch_result.failed}[/], "
        f"[dim]跳过 {batch_result.skipped}[/], "
        f"耗时 {batch_result.duration:.1f}s"
    )


@cli.command("batch-status")
@click.pass_context
def batch_status(ctx: click.Context) -> None:
    """查看批量处理任务状态。"""
    config: MediaLensConfig = ctx.obj["config"]
    engine = BatchEngine(config=config)
    jobs = engine.list_jobs()

    if not jobs:
        console.print("[yellow]暂无批量任务记录[/]")
        return

    from datetime import datetime

    table = Table(title="批量任务状态", border_style="blue")
    table.add_column("任务ID", style="cyan", no_wrap=True)
    table.add_column("总文件", width=8)
    table.add_column("已完成", width=8)
    table.add_column("状态", width=12)
    table.add_column("开始时间", width=20)
    table.add_column("完成时间", width=20)

    for job in jobs:
        job_id = job.get("job_id", "")[:20]
        total = job.get("total_files", 0)
        completed = job.get("completed_files", 0)
        status_val = job.get("status", "unknown")

        status_style = {
            "running": "[green]运行中[/]",
            "completed": "[blue]已完成[/]",
            "failed": "[red]失败[/]",
            "cancelled": "[yellow]已取消[/]",
        }.get(status_val, f"[dim]{status_val}[/]")

        started = job.get("started_at", "") or "-"
        completed_at = job.get("completed_at", "") or "-"

        table.add_row(job_id, str(total), str(completed), status_style, started, completed_at)

    console.print(table)


@cli.command("batch-resume")
@click.argument("job_id", required=True)
@click.option("--scrape/--no-scrape", default=True, help="是否刮削")
@click.option("--organize", default=None, type=click.Path(), help="整理目标目录")
@click.option("--workers", default=2, type=int, help="并行工作数")
@click.pass_context
def batch_resume(
    ctx: click.Context,
    job_id: str,
    scrape: bool,
    organize: Optional[str],
    workers: int,
) -> None:
    """从断点续传批量任务。

    JOB_ID 是任务ID，可通过 batch-status 查看。
    """
    config: MediaLensConfig = ctx.obj["config"]
    engine = BatchEngine(config=config)

    # 加载 checkpoint
    checkpoint = engine.load_checkpoint(job_id)
    if checkpoint is None:
        console.print(f"[red]任务不存在:[/] {job_id}")
        console.print("请先使用 [bold]batch-run[/] 启动一个任务，或通过 [bold]batch-status[/] 查看任务列表。")
        sys.exit(1)

    done_count = len(checkpoint["done"])
    failed_count = len(checkpoint["failed"])
    console.print(f"任务 [bold]{job_id}[/]: 已完成 {done_count}, 失败 {failed_count}")

    # 从 checkpoint 重建文件列表
    all_files: list[str] = []
    for cp in checkpoint["all"]:
        if cp["status"] in ("failed", "pending", "processing"):
            all_files.append(cp["file_path"])

    if not all_files:
        console.print("[yellow]所有文件已处理完成，无需续传[/]")
        return

    console.print(f"待续传文件: {len(all_files)} 个")

    opts = BatchOptions(
        scrape=scrape,
        organize=bool(organize),
        target_dir=organize or "",
        dry_run=ctx.obj.get("dry_run", True),
        max_workers=workers,
        continue_on_error=True,
    )

    # 使用原 job_id 续传
    result = engine.run_batch(all_files, options=opts, job_id=job_id)

    console.print(f"\n[bold]续传完成:[/] "
        f"成功 {result.succeeded}, 失败 {result.failed}, "
        f"耗时 {result.duration:.1f}s"
    )


# 保留旧版 batch 命令作为别名
@cli.command("batch")
@click.argument("path", required=True, type=click.Path(exists=True))
@click.option("--scrape/--no-scrape", default=False, help="匹配后自动刮削 NFO")
@click.option("--output", "-o", default=None, type=click.Path(), help="输出目录")
@click.option("--tmdb-key", help="TMDB API Key")
@click.option("--language", default=None, help="TMDB 语言")
@click.pass_context
def batch_legacy(
    ctx: click.Context,
    path: str,
    scrape: bool,
    output: Optional[str],
    tmdb_key: Optional[str],
    language: Optional[str],
) -> None:
    """（旧版）批量扫描目录并处理所有视频文件。

    建议使用 batch-run 替代此命令，功能更强大。
    """
    # 转发到 batch-run
    ctx.invoke(
        batch_run,
        path=path,
        scrape=scrape,
        organize=output,
        mode="hardlink",
        dry_run=ctx.obj.get("dry_run", True),
        workers=1,
        no_llm=False,
        tmdb_key=tmdb_key,
        language=language,
    )


# ===========================================================================
# scrape 命令
# ===========================================================================


@cli.command()
@click.argument("path", required=True, type=click.Path(exists=True))
@click.option("--tmdb-key", help="TMDB API Key")
@click.option("--images", is_flag=True, default=False, help="同时下载图片")
@click.option("--overwrite", is_flag=True, default=False, help="覆盖已存在的 NFO")
@click.option("--output", "-o", default=None, type=click.Path(), help="输出目录（默认文件所在目录）")
@click.pass_context
def scrape(
    ctx: click.Context,
    path: str,
    tmdb_key: Optional[str],
    images: bool,
    overwrite: bool,
    output: Optional[str],
) -> None:
    """刮削元数据（生成 NFO + 下载图片）

    对已匹配的文件，从 TMDB 获取详细信息，
    生成 Kodi/Emby/Jellyfin 兼容的 NFO XML 文件。
    """
    config: MediaLensConfig = ctx.obj["config"]
    api_key = tmdb_key or config.tmdb_api_key
    if not api_key:
        console.print("[red]错误:[/] TMDB API Key 未配置。")
        sys.exit(1)

    # 读取已保存的匹配结果
    db = DatabaseManager(db_path=config.db_path)
    db.initialize()
    match_row = db.get_match_by_path(str(Path(path).resolve()))

    if match_row is None:
        console.print("[yellow]警告:[/] 数据库中没有匹配记录。请先运行 match 命令。")
        return

    # 重建 MatchResult
    from medialens.models import MatchSource, MediaType

    result = MatchResult(
        matched=True,
        media_type=MediaType(match_row.get("media_type", "movie")),
        tmdb_id=match_row.get("tmdb_id", 0) or 0,
        title=match_row.get("matched_title", ""),
        original_title="",
        year=match_row.get("matched_year"),
        source=MatchSource(match_row.get("match_source", "tmdb_fuzzy")),
        confidence=match_row.get("confidence", 0.0),
        overview=match_row.get("overview", ""),
        poster_path=match_row.get("poster_path"),
        backdrop_path=match_row.get("backdrop_path"),
        season=match_row.get("parsed_season"),
        episode=match_row.get("parsed_episode"),
    )

    # 确定输出目录
    target_dir = output or str(Path(path).parent)

    # 初始化 NFO 生成器
    nfo = NfoGenerator(tmdb_api_key=api_key, language=config.tmdb_language)

    console.print(f"[bold]刮削:[/] {Path(path).name}")
    console.print(f"[bold]目标:[/] {result.title} ({result.year or '?'})")
    console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]生成 NFO...", total=None)

        scrape_status = _do_scrape(
            nfo, result, target_dir,
            download_images=images,
            overwrite=overwrite,
        )

        progress.update(task, description="[green]完成")

    console.print(f"刮削状态: {scrape_status}")


# ===========================================================================
# organize 命令
# ===========================================================================


@cli.command()
@click.argument("path", required=True, type=click.Path(exists=True))
@click.option("--target-dir", required=True, type=click.Path(), help="目标目录（必填）")
@click.option("--mode", default=None, type=click.Choice(["hardlink", "copy", "move"]), help="整理模式")
@click.option("--strategy", default=None, type=click.Choice(["year", "first_letter", "flat"]), help="目录策略")
@click.option("--movie-template", default=None, help="电影命名模板")
@click.option("--tv-template", default=None, help="电视剧命名模板")
@click.pass_context
def organize(
    ctx: click.Context,
    path: str,
    target_dir: str,
    mode: Optional[str],
    strategy: Optional[str],
    movie_template: Optional[str],
    tv_template: Optional[str],
) -> None:
    """整理媒体文件。

    根据匹配结果和目标目录结构，对媒体文件执行硬链接/复制/移动操作。
    """
    config: MediaLensConfig = ctx.obj["config"]
    dry_run = ctx.obj.get("dry_run", False)
    concrete_mode = mode or config.organize_mode

    # 读取匹配结果
    db = DatabaseManager(db_path=config.db_path)
    db.initialize()

    target_path = Path(path)
    if target_path.is_dir():
        # 目录模式：使用 DirectoryOrganizer
        from medialens.detector.format_detector import get_video_files

        video_files = get_video_files(path)
        if not video_files:
            console.print("[yellow]未找到视频文件[/]")
            return

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]整理中...", total=len(video_files))
            operations: list[tuple[str, str, str]] = []

            for vf in video_files:
                match_row = db.get_match_by_path(str(Path(vf).resolve()))
                if match_row is None:
                    # 无匹配记录，使用 DirectoryOrganizer 按文件名整理
                    title = Path(vf).stem
                    organizer = DirectoryOrganizer()
                    concrete_strategy = strategy or "year"
                    dest_dir = organizer.organize_movie_folder(
                        title, 0, target_dir, concrete_strategy
                    )
                    dest = str(Path(dest_dir) / Path(vf).name)
                    operations.append((vf, dest, "[yellow]无匹配[/]"))
                else:
                    result = MatchResult(
                        matched=True,
                        media_type=type(
                            "MediaType", (), {"value": match_row.get("media_type", "movie")}
                        )(),
                        tmdb_id=match_row.get("tmdb_id", 0) or 0,
                        title=match_row.get("matched_title", ""),
                        original_title="",
                        year=match_row.get("matched_year"),
                        source=MatchSource.CACHE_HIT,
                        confidence=100.0,
                    )
                    fm = FileManager()
                    rename_config = RenameConfig()
                    rename_config.create_hardlink = concrete_mode == "hardlink"
                    rename_config.keep_original = concrete_mode != "move"

                    op = fm.organize_file(
                        source=vf,
                        target_dir=target_dir,
                        result=result,
                        config=rename_config,
                        dry_run=dry_run,
                    )
                    operations.append((vf, str(op.target_path), "success" if op.success else op.error or "failed"))

                progress.advance(task)

        # 输出结果表格
        table = Table(title="整理结果", border_style="blue")
        table.add_column("源文件", style="white")
        table.add_column("目标路径", style="green")
        table.add_column("状态")
        for src, dst, status in operations:
            status_style = f"[green]{status}[/]" if status == "success" else f"[red]{status}[/]"
            table.add_row(Path(src).name, dst, status_style)
        console.print(table)

    else:
        # 单文件模式
        match_row = db.get_match_by_path(str(target_path.resolve()))
        if match_row is None:
            console.print("[yellow]警告:[/] 数据库中没有匹配记录。请先运行 match 命令。")
            return

        result = MatchResult(
            matched=True,
            media_type=MediaType(match_row.get("media_type", "movie")),
            tmdb_id=match_row.get("tmdb_id", 0) or 0,
            title=match_row.get("matched_title", ""),
            original_title="",
            year=match_row.get("matched_year"),
            source=MatchSource.CACHE_HIT,
            confidence=100.0,
        )

        fm = FileManager()
        rename_config = RenameConfig()
        rename_config.create_hardlink = concrete_mode == "hardlink"
        rename_config.keep_original = concrete_mode != "move"

        op = fm.organize_file(
            source=path,
            target_dir=target_dir,
            result=result,
            config=rename_config,
            dry_run=dry_run,
        )

        if dry_run:
            console.print(f"\U0001f537 [bold]预览:[/] {path} → {op.target_path}")
        elif op.success:
            console.print(f"\u2705 [bold]整理成功:[/] {op.target_path}")
        else:
            console.print(f"\u274c [red]整理失败:[/] {op.error}")

    console.print(f"\n[dim]整理模式: {concrete_mode}" + (" (预览)[/]" if dry_run else "[/]"))


# ===========================================================================
# config 命令
# ===========================================================================


@cli.group()
def config() -> None:
    """查看/设置配置"""
    pass


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """显示当前配置"""
    cfg: MediaLensConfig = ctx.obj["config"]
    config_path = ctx.obj.get("config_path", "config.yaml")

    table = Table(title=f"当前配置（来源: {config_path}）", border_style="blue")
    table.add_column("配置项", style="cyan", no_wrap=True)
    table.add_column("值", style="white")

    table.add_row("TMDB API Key", cfg.tmdb_api_key[:8] + "..." if cfg.tmdb_api_key else "[red]未设置[/]")
    table.add_row("TMDB 语言", cfg.tmdb_language)
    table.add_row("LLM Provider", cfg.llm_provider)
    table.add_row("LLM Model", cfg.llm_model)
    table.add_row("LLM API Key", "[green]已设置[/]" if cfg.llm_api_key else "[yellow]未设置[/]")
    table.add_row("TMDB 置信度阈值", str(cfg.tmdb_confidence_threshold))
    table.add_row("LLM 置信度阈值", str(cfg.llm_confidence_threshold))
    table.add_row("预览模式", str(cfg.dry_run))
    table.add_row("整理模式", cfg.organize_mode)
    table.add_row("数据库路径", cfg.db_path)
    table.add_row("数据目录", cfg.data_dir)

    console.print(table)


@config.command("set")
@click.argument("key_value", required=True, metavar="KEY=VALUE")
@click.pass_context
def config_set(ctx: click.Context, key_value: str) -> None:
    """设置配置项（格式 KEY=VALUE）

    支持的配置项：
    \b
    - tmdb.api_key
    - tmdb.language
    - llm.provider
    - llm.api_key
    - llm.model
    - thresholds.tmdb_confidence
    - thresholds.llm_confidence
    - file_management.dry_run
    - file_management.organize_mode
    """
    if "=" not in key_value:
        console.print("[red]错误:[/] 格式应为 KEY=VALUE")
        sys.exit(1)

    key, value = key_value.split("=", 1)

    config_path: str = ctx.obj.get("config_path", "config.yaml")
    config_file = Path(config_path)

    if not config_file.exists():
        console.print(f"[red]配置文件不存在:[/] {config_path}")
        console.print("请先运行 [bold]medialens config --init[/]")
        sys.exit(1)

    # 加载现有 YAML
    import yaml

    with open(config_file, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f) or {}

    # 将扁平键转换为嵌套结构
    parts = key.split(".")
    current = yaml_data
    for part in parts[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]

    # 尝试转换为合适的类型
    raw_value: Any = value
    if value.lower() in ("true", "false"):
        raw_value = value.lower() == "true"
    elif value.isdigit():
        raw_value = int(value)

    current[parts[-1]] = raw_value

    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True)

    console.print(f"\u2705 已设置 {key} = {value} (保存至 {config_path})")


@config.command("init")
@click.option("--force", is_flag=True, default=False, help="覆盖已存在的配置文件")
@click.argument("output_path", required=False, default="config.yaml")
@click.pass_context
def config_init(ctx: click.Context, force: bool, output_path: str) -> None:
    """生成默认配置文件"""
    path = Path(output_path)
    if path.exists() and not force:
        console.print(f"[yellow]配置文件已存在:[/] {output_path}")
        console.print("使用 --force 覆盖")
        return

    result = generate_default_config(output_path)
    console.print(f"\u2705 默认配置文件已生成: [bold]{result}[/]")
    console.print("请编辑配置文件，填写 TMDB API Key 和其他参数。")


# ===========================================================================
# stats 命令
# ===========================================================================


@cli.command()
@click.pass_context
def stats(ctx: click.Context) -> None:
    """查看统计信息

    显示总匹配数、缓存命中率、LLM 调用次数等统计数据。
    """
    config: MediaLensConfig = ctx.obj["config"]

    db = DatabaseManager(db_path=config.db_path)
    db.initialize()

    try:
        stats_data = db.get_stats()
    except Exception as exc:
        console.print(f"[red]获取统计信息失败:[/] {exc}")
        sys.exit(1)

    console.print()

    # 总览面板
    overview_text = Text()
    overview_text.append(f"总匹配数: {stats_data.get('total_matches', 0)}", style="bold cyan")
    console.print(Panel(overview_text, title="MediaLens 统计", border_style="cyan"))

    # 详细表格
    table = Table(border_style="blue")
    table.add_column("指标", style="cyan", no_wrap=True)
    table.add_column("数值", style="white")

    table.add_row("总匹配数", str(stats_data.get("total_matches", 0)))
    table.add_row("缓存命中数", str(stats_data.get("cache_hits", 0)))
    table.add_row(
        "缓存命中率",
        f"{stats_data.get('cache_hit_rate', 0) * 100:.2f}%"
        if stats_data.get("cache_hit_rate")
        else "0.00%",
    )
    table.add_row("平均置信度", f"{stats_data.get('avg_confidence', 0):.2f}")

    # 来源分布
    source_dist = stats_data.get("source_distribution", {})
    for source, count in source_dist.items():
        table.add_row(f"  来源: {source}", str(count))

    table.add_row("")
    table.add_row("LLM 调用次数", str(stats_data.get("llm_call_count", 0)))
    table.add_row("LLM 成功次数", str(stats_data.get("llm_success_count", 0)))
    table.add_row("文件操作次数", str(stats_data.get("file_operation_count", 0)))

    console.print(table)


# ===========================================================================
# cache 命令
# ===========================================================================


@cli.group()
def cache() -> None:
    """缓存管理"""
    pass


@cache.command("list")
@click.option("--limit", default=20, type=int, help="显示数量限制")
@click.pass_context
def cache_list(ctx: click.Context, limit: int) -> None:
    """查看缓存列表"""
    config: MediaLensConfig = ctx.obj["config"]

    db = DatabaseManager(db_path=config.db_path)
    db.initialize()

    # 获取缓存条目（缓存命中 + 已匹配记录）
    rows = db._query_all(
        "SELECT id, file_path, parsed_title, matched_title, "
        "match_source, confidence, created_at "
        "FROM match_history ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )

    if not rows:
        console.print("[yellow]暂无缓存记录[/]")
        return

    table = Table(title=f"缓存列表（最近 {len(rows)} 条）", border_style="blue")
    table.add_column("ID", width=6)
    table.add_column("文件路径", style="white")
    table.add_column("解析标题", style="green")
    table.add_column("匹配标题", style="green")
    table.add_column("来源", width=16)
    table.add_column("置信度", width=8)
    table.add_column("时间", width=20)

    for row in rows:
        table.add_row(
            str(row.get("id", "")),
            row.get("file_path", ""),
            row.get("parsed_title", "") or "-",
            row.get("matched_title", "") or "-",
            row.get("match_source", "") or "-",
            f"{row.get('confidence', 0):.1f}",
            row.get("created_at", "") or "-",
        )

    console.print(table)


@cache.command("clear")
@click.option("--force", is_flag=True, default=False, help="确认清空缓存")
@click.pass_context
def cache_clear(ctx: click.Context, force: bool) -> None:
    """清空缓存"""
    if not force:
        console.print("[yellow]警告:[/] 此操作将清空所有匹配缓存记录，不可恢复！")
        console.print("请使用 [bold]--force[/] 确认操作。")
        return

    config: MediaLensConfig = ctx.obj["config"]

    db = DatabaseManager(db_path=config.db_path)
    db.initialize()

    with db._lock:
        c = db.conn.cursor()
        c.execute("DELETE FROM match_history")
        c.execute("DELETE FROM llm_call_log")
        c.execute("DELETE FROM file_operations")
        db.conn.commit()

    console.print("\u2705 缓存已清空")


# ===========================================================================
# 入口
# ===========================================================================

if __name__ == "__main__":
    cli()
