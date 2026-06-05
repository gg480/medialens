"""
格式检测模块

检测媒体文件的格式类型（单文件/蓝光原盘/ISO/剧集），
决定 Orchestrator 使用哪种解析策略。

本模块不依赖外部库，仅使用 pathlib 和标准库。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from medialens.models import FileFormat

# 支持的视频/媒体文件扩展名
VIDEO_EXTENSIONS: set[str] = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov",
    ".wmv", ".flv", ".webm", ".iso", ".bdiso", ".m4v",
    ".divx", ".xvid", ".rmvb", ".mpeg", ".mpg",
}

# 不需要读取内容的"可见文件扩展名"——仅通过扩展名即可快速判定的格式
_ISO_EXTENSIONS: set[str] = {".iso", ".bdiso"}
_SINGLE_VIDEO_EXTENSIONS: set[str] = {
    ".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov",
    ".wmv", ".flv", ".webm", ".m4v", ".divx", ".xvid",
    ".rmvb", ".mpeg", ".mpg",
}

# BDMV 目录结构中必须存在的子目录
_BDMV_REQUIRED_SUBDIRS: set[str] = {"STREAM", "PLAYLIST", "CLIPINF"}


def _is_video_extension(path: Path) -> bool:
    """检查文件扩展名是否为支持的媒体格式"""
    return path.suffix.lower() in VIDEO_EXTENSIONS


def detect_format(path: str) -> FileFormat:
    """
    检测文件/路径的媒体格式类型。

    检测逻辑（按优先级）：
    1. 目录 → 检查 BDMV 结构 → BLURAY_BDMV
    2. 目录 → 检查多文件剧集 → TV_SEASON
    3. 目录 → UNKNOWN
    4. 文件 → .iso/.bdiso → BDISO
    5. 文件 → 常规视频扩展名 → 检查剧集标记 → TV_EPISODE or SINGLE_FILE
    6. 文件 → UNKNOWN

    Args:
        path: 文件或目录路径。

    Returns:
        FileFormat 枚举值。
    """
    try:
        p = Path(path)
    except (OSError, ValueError) as e:
        return FileFormat.UNKNOWN

    if not p.exists():
        return FileFormat.UNKNOWN

    try:
        if p.is_dir():
            return _detect_directory(p)
        else:
            return _detect_file(p)
    except PermissionError:
        return FileFormat.UNKNOWN
    except OSError:
        return FileFormat.UNKNOWN


def _detect_directory(directory: Path) -> FileFormat:
    """检测目录格式"""
    # 优先级 1：BDMV 目录结构
    if is_bdmv_structure(str(directory)):
        return FileFormat.BLURAY_BDMV

    # 优先级 2：多文件剧集（TV_SEASON）
    video_files = get_video_files(str(directory))
    if len(video_files) >= 2:
        episode_count = sum(
            1 for f in video_files if is_episode_filename(f)
        )
        # 至少有两个视频文件包含剧集标记才认定为剧集季目录
        if episode_count >= 2:
            return FileFormat.TV_SEASON

    return FileFormat.UNKNOWN


def _detect_file(file_path: Path) -> FileFormat:
    """检测文件格式"""
    suffix = file_path.suffix.lower()

    # 优先级 1：ISO/BDISO 镜像
    if suffix in _ISO_EXTENSIONS:
        return FileFormat.BDISO

    # 优先级 2：常规视频文件
    if suffix in _SINGLE_VIDEO_EXTENSIONS:
        if is_episode_filename(file_path.name):
            return FileFormat.TV_EPISODE
        return FileFormat.SINGLE_FILE

    return FileFormat.UNKNOWN


def is_bdmv_structure(directory: str) -> bool:
    """
    检查目录是否为 BDMV 蓝光原盘结构。

    判断标准：
    - 存在 BDMV/ 子目录
    - BDMV/ 下同时存在 STREAM/, PLAYLIST/, CLIPINF/ 三个子目录

    Args:
        directory: 待检查的目录路径。

    Returns:
        bool: 是否为 BDMV 目录结构。
    """
    try:
        base = Path(directory)
        if not base.is_dir():
            return False

        bdmv_dir = base / "BDMV"
        if not bdmv_dir.is_dir():
            return False

        # 检查 BDMV 下是否包含必需的子目录
        for subdir in _BDMV_REQUIRED_SUBDIRS:
            if not (bdmv_dir / subdir).is_dir():
                return False

        return True

    except PermissionError:
        return False
    except OSError:
        return False


def is_episode_filename(filename: str) -> bool:
    """
    通过正则判断文件名是否包含剧集标记。

    支持的模式：
    - SxxExx: S01E01, s01e01, S1E1
    - Exx: E01, e01 (无季号，单独集号)
    - 第x集: 第01集, 第1集
    - 多集连播: S01E01E02, S01E01-E02

    Args:
        filename: 文件名（含扩展名）。

    Returns:
        bool: 是否包含剧集标记。
    """
    name = filename.strip()

    # 先移除扩展名再检测
    stem = Path(name).stem

    # SxxExx 模式（标准剧集标记）
    if re.search(r'[Ss]\d{1,2}[Ee]\d{1,4}', stem):
        return True

    # Exx 模式（无季号，单独集号）——只在文件名不含 Sxx 时匹配
    # 要求 E 后面至少 2 位数字避免误匹配，如 "E.T.mp4"
    if re.search(r'(?<![Ss\d])[Ee]\d{2,4}(?![Pp]|[Ee]\d)', stem):
        return True

    # "第x集" 模式
    if re.search(r'第\d{1,4}[集话]', stem):
        return True

    # Sxx 模式（只有季号无集号，如 "Season 01"）
    if re.search(r'[Ss]eason\s*\d{1,2}', stem, re.IGNORECASE):
        return True

    return False


def get_video_files(directory: str) -> List[str]:
    """
    递归获取目录下的所有视频文件（返回相对/绝对路径字符串）。

    遍历目录树，收集所有扩展名在 VIDEO_EXTENSIONS 中的文件。

    Args:
        directory: 目录路径。

    Returns:
        视频文件路径列表（字符串）。
    """
    try:
        base = Path(directory)
        if not base.is_dir():
            return []

        files: List[str] = []
        for entry in base.rglob("*"):
            try:
                if entry.is_file() and _is_video_extension(entry):
                    files.append(str(entry))
            except (PermissionError, OSError):
                continue

        return files

    except PermissionError:
        return []
    except OSError:
        return []


def is_bdmv_file(m2ts_path: str) -> bool:
    """
    检查文件是否处于 BDMV 目录结构下的 m2ts 文件。

    用于判断某个 m2ts 文件是否需要 BlurayParser 的特殊处理。

    Args:
        m2ts_path: 文件路径。

    Returns:
        bool: 是否在 BDMV/STREAM/ 目录下。
    """
    try:
        p = Path(m2ts_path).resolve()
        # 检查路径中是否存在 BDMV/STREAM/ 层级
        parts = p.parts
        for i, part in enumerate(parts):
            if part.upper() == "BDMV":
                if i + 1 < len(parts) and parts[i + 1].upper() == "STREAM":
                    return True
        return False
    except (OSError, ValueError):
        return False
