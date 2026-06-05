"""
BlurayParser - 蓝光原盘 BDMV 解析器

处理 BDMV 蓝光原盘目录结构。
核心策略：标题信息在父目录名中，而非文件名。
- 从 BDMV 目录的父目录名提取标题
- 清理常见后缀（BluRay, REMUX, 1080p 等）
- 提取年份
- 扫描 BDMV/STREAM/ 下的 .m2ts 文件
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from medialens.models import FileFormat, ParsedMedia
from medialens.parser.base import BaseParser


class BlurayParser(BaseParser):
    """蓝光原盘 BDMV 解析器"""

    def can_handle(self, file_format: FileFormat) -> bool:
        return file_format == FileFormat.BLURAY_BDMV

    def parse(self, path: str) -> ParsedMedia:
        raw_path = self._make_path(path)
        bdmv_dir = self._find_bdmv_dir(raw_path)

        # 父目录名（即蓝光原盘标题的来源）
        parent_dir = bdmv_dir.parent
        parent_name = parent_dir.name

        # 从父目录名提取标题和年份
        title = self._clean_title(parent_name)
        year = self._extract_year(parent_name)

        # 扫描 STREAM/ 下 m2ts 文件
        stream_dir = bdmv_dir / "STREAM"
        m2ts_files, total_size = self._scan_m2ts(stream_dir)

        # 找出最大的 m2ts 文件（主电影）
        largest_m2ts, largest_name = None, None
        for fpath in m2ts_files:
            try:
                fsize = fpath.stat().st_size
                if largest_m2ts is None or fsize > largest_m2ts:
                    largest_m2ts = fsize
                    largest_name = fpath.name
            except OSError:
                continue

        result = ParsedMedia(
            raw_path=raw_path,
            raw_filename=parent_name,
            file_format=FileFormat.BLURAY_BDMV,
            title=title or parent_name,
            year=year,
            source="BluRay",
            bdmv_parent=parent_name,
            m2ts_count=len(m2ts_files),
            largest_m2ts=largest_name,
            confidence=self._calc_confidence(title, year, m2ts_files),
        )

        return result

    def _find_bdmv_dir(self, path: Path) -> Path:
        """
        找到 BDMV 目录路径。

        支持两种输入：
        1. 传入 BDMV 目录本身：/path/to/Movie/BDMV/
        2. 传入父目录：/path/to/Movie/
        """
        if path.name.upper() == "BDMV":
            return path
        bdmv_candidate = path / "BDMV"
        if bdmv_candidate.is_dir():
            return bdmv_candidate
        # 如果都不存在，返回传入的 path（让调用方处理错误）
        return path

    def _scan_m2ts(self, stream_dir: Path) -> tuple[list[Path], int]:
        """
        扫描 STREAM/ 目录下的 .m2ts 文件。

        返回 (文件列表, 总大小字节)
        """
        m2ts_files: list[Path] = []
        total_size = 0

        if not stream_dir.is_dir():
            return m2ts_files, total_size

        try:
            for entry in os.scandir(str(stream_dir)):
                if entry.is_file() and entry.name.lower().endswith(".m2ts"):
                    fpath = Path(entry.path)
                    m2ts_files.append(fpath)
                    total_size += entry.stat().st_size
        except PermissionError:
            pass

        # 按文件名排序（00000.m2ts → 00001.m2ts）
        m2ts_files.sort(key=lambda p: p.name)
        return m2ts_files, total_size

    def _calc_confidence(
        self,
        title: Optional[str],
        year: Optional[int],
        m2ts_files: list[Path],
    ) -> float:
        """计算 BDMV 解析置信度"""
        score = 0.0
        if title:
            score += 0.4
        if year:
            score += 0.2
        if m2ts_files:
            score += 0.3  # 有 m2ts 文件说明结构完整
            if len(m2ts_files) >= 1:
                score += 0.1  # 至少有一个 m2ts 文件
        return min(score, 1.0)
