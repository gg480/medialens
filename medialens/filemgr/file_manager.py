"""
文件操作引擎 - 硬链接/复制/移动/目录整理

负责对媒体文件执行文件系统操作（硬链接、复制、移动），
以及按策略生成目标目录结构。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from medialens.models import (
    FileOperation,
    FileFormat,
    MatchResult,
    MatchSource,
    MediaType,
    ParsedMedia,
    RenameConfig,
)


# ---------------------------------------------------------------------------
# Windows 非法文件名字符
# ---------------------------------------------------------------------------
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


class FileManager:
    """文件管理器

    核心职责：
    - 根据 MatchResult 和 RenameConfig 生成目标路径
    - 执行硬链接/复制/移动操作
    - 批量整理目录
    """

    # ------------------------------------------------------------------
    # 公共方法
    # ------------------------------------------------------------------

    def organize_file(
        self,
        source: str,
        target_dir: str,
        result: MatchResult,
        config: RenameConfig,
        dry_run: bool = True,
    ) -> FileOperation:
        """对单个已匹配的媒体文件执行整理操作。

        流程：
        1. 根据 MatchResult 生成目标文件名和路径
        2. 确保目标目录存在
        3. 按 config 指定的操作类型执行 hardlink / copy / move
        4. 返回 FileOperation 记录
        """
        source_path = Path(source)
        if not source_path.exists():
            return FileOperation(
                source_path=source_path,
                target_path=source_path,
                operation=self._determine_operation(config),
                dry_run=dry_run,
                success=False,
                error=f"源文件不存在: {source}",
            )

        target_full_path = self.generate_target_path(source, target_dir, result, config)

        target = Path(target_full_path)
        self.ensure_dir(str(target.parent))

        op_type = self._determine_operation(config)

        if dry_run:
            success = self.dry_run_operation(source, str(target))
        else:
            if op_type == "hardlink":
                success = self.create_hardlink(source, str(target))
            elif op_type == "copy":
                success = self.copy_file(source, str(target))
            elif op_type == "move":
                success = self.move_file(source, str(target))
            else:
                success = False

        return FileOperation(
            source_path=source_path,
            target_path=target,
            operation=op_type,
            dry_run=dry_run,
            success=success,
            error=None if success else f"{op_type} 操作失败",
        )

    def generate_target_path(
        self, source: str, target_dir: str, result: MatchResult, config: RenameConfig
    ) -> str:
        """生成目标完整路径（目录 + 文件名）。

        规则（含质量标签）：
        - 电影：{target_dir}/{title} ({year})/{title} ({year}) [{quality}].{ext}
        - 电视剧：{target_dir}/{title}/Season {season:02d}/{title}
                 - S{season:02d}E{episode:02d} [{quality}].{ext}

        质量标签从源文件名中提取，格式如 "BluRay.1080p"、"WEB-DL.2160p"。
        """
        source_path = Path(source)
        ext = source_path.suffix

        safe_title = self.safe_filename(result.title)
        quality = self._extract_quality_from_source(source)

        if result.media_type == MediaType.MOVIE:
            folder_parts = [safe_title]
            if result.year:
                folder_parts.append(f"({result.year})")
            folder_name = " ".join(folder_parts)

            file_parts = [safe_title]
            if result.year:
                file_parts.append(f"({result.year})")
            file_parts.append(f"[{quality}]")
            file_name = " ".join(file_parts) + ext

            return str(Path(target_dir) / folder_name / file_name)

        elif result.media_type == MediaType.TV:
            season = result.season or 1
            episode = result.episode or 1

            tv_folder = self.safe_filename(result.title)
            season_dir = f"Season {season:02d}"

            file_name = (
                f"{safe_title} - "
                f"S{season:02d}E{episode:02d} - "
                f"[{quality}]{ext}"
            )

            return str(Path(target_dir) / tv_folder / season_dir / file_name)

        else:
            # 未知媒体类型，直接放在 target_dir 下
            file_name = f"{safe_title}{ext}"
            return str(Path(target_dir) / file_name)

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------

    def create_hardlink(self, src: str, dst: str) -> bool:
        """创建硬链接"""
        try:
            if Path(dst).exists():
                return False
            os.link(src, dst)
            return True
        except (OSError, PermissionError) as exc:
            return False

    def copy_file(self, src: str, dst: str) -> bool:
        """复制文件（保留元数据）"""
        try:
            shutil.copy2(src, dst)
            return True
        except (OSError, PermissionError, shutil.Error) as exc:
            return False

    def move_file(self, src: str, dst: str) -> bool:
        """移动文件"""
        try:
            shutil.move(src, dst)
            return True
        except (OSError, PermissionError, shutil.Error) as exc:
            return False

    def dry_run_operation(self, src: str, dst: str) -> bool:
        """仅预览：检查源文件和目标路径合法性，不执行实际操作"""
        src_path = Path(src)
        if not src_path.exists():
            return False
        # 硬链接要求源和目标在同一个文件系统
        if os.name == "nt":
            src_drive = os.path.splitdrive(src)[0]
            dst_drive = os.path.splitdrive(dst)[0]
            if src_drive != dst_drive:
                return False
        return True

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def ensure_dir(self, directory: str) -> bool:
        """确保目录存在，不存在则创建"""
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, PermissionError) as exc:
            return False

    @staticmethod
    def safe_filename(name: str) -> str:
        """清理文件名中的非法字符（Windows: \\/:*?\"<>|）"""
        cleaned = _INVALID_FILENAME_CHARS.sub("", name)
        cleaned = cleaned.strip()
        # 避免空文件名
        if not cleaned:
            cleaned = "untitled"
        return cleaned

    def get_file_size(self, path: str) -> int:
        """获取文件大小（字节）"""
        try:
            return Path(path).stat().st_size
        except (OSError, FileNotFoundError):
            return 0

    def get_quality_tag(self, parsed: ParsedMedia) -> str:
        """从 ParsedMedia 生成质量标签

        格式：``{source}.{resolution}``
        例如：BluRay.1080p、WEB-DL.2160p、HDTV.720p

        当某一项缺失时，只返回另一项；两项都缺失时返回 "Unknown"。
        """
        parts = []
        if parsed.source:
            parts.append(parsed.source)
        if parsed.resolution:
            parts.append(parsed.resolution)
        return ".".join(parts) if parts else "Unknown"

    def organize_directory(
        self,
        paths: list[str],
        target_dir: str,
        organizer: "DirectoryOrganizer",
    ) -> list[FileOperation]:
        """批量整理多个文件到按策略组织的目录中。

        对每个文件：
        1. 提取文件名中的标题（去除扩展名和常见后缀）
        2. 使用 DirectoryOrganizer 确定目标目录
        3. 将文件放置到目标目录中（不重命名文件本身）
        """
        results: list[FileOperation] = []

        for path_str in paths:
            src = Path(path_str)
            if not src.exists():
                results.append(
                    FileOperation(
                        source_path=src,
                        target_path=src,
                        operation="copy",
                        dry_run=True,
                        success=False,
                        error=f"源文件不存在: {path_str}",
                    )
                )
                continue

            title = self._guess_title_from_filename(src.stem)
            year = self._guess_year_from_filename(src.stem)
            dest_dir = organizer.organize_movie_folder(
                title, year, target_dir, strategy="year"
            )
            dest = Path(dest_dir) / src.name

            self.ensure_dir(dest_dir)

            op = self._determine_operation(RenameConfig())
            if op == "hardlink":
                success = self.create_hardlink(str(src), str(dest))
            elif op == "copy":
                success = self.copy_file(str(src), str(dest))
            else:
                success = self.move_file(str(src), str(dest))

            results.append(
                FileOperation(
                    source_path=src,
                    target_path=dest,
                    operation=op,
                    dry_run=False,
                    success=success,
                )
            )

        return results

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _determine_operation(self, config: RenameConfig) -> str:
        """根据配置确定操作类型"""
        if config.create_hardlink:
            return "hardlink"
        if config.keep_original:
            return "copy"
        return "move"

    def _extract_quality_from_source(self, source: str) -> str:
        """从源文件名中提取质量标签

        用常见关键词在文件名中匹配 source 和 resolution。
        """
        filename = Path(source).stem
        source_keywords = {
            "bluray": "BluRay",
            "web-dl": "WEB-DL",
            "webdl": "WEB-DL",
            "hdtv": "HDTV",
            "dvd": "DVD",
            "dvdrip": "DVD",
            "hdr": "HDR",
            "remux": "REMUX",
        }
        res_keywords = {
            "2160p": "2160p",
            "1080p": "1080p",
            "720p": "720p",
            "480p": "480p",
        }

        found_source = None
        found_res = None

        lower = filename.lower()
        # 按长度降序匹配，避免 BluRay 比 REMUX 先被匹配
        for kw, tag in sorted(source_keywords.items(), key=lambda x: -len(x[0])):
            if kw in lower:
                found_source = tag
                break

        for kw, tag in sorted(res_keywords.items(), key=lambda x: -len(x[0])):
            if kw in lower:
                found_res = tag
                break

        parts = [p for p in (found_source, found_res) if p]
        return ".".join(parts) if parts else "Unknown"

    @staticmethod
    def _guess_title_from_filename(stem: str) -> str:
        """从文件名去除年份和常见质量后缀后猜测标题"""
        # 去除年份（4位数字）
        name = re.sub(r"\b\d{4}\b", "", stem)
        # 去除常见质量关键词
        for kw in [
            "bluray", "web-dl", "webdl", "hdtv", "dvd", "dvdrip",
            "2160p", "1080p", "720p", "480p", "remux", "hdr",
            "x264", "x265", "h264", "h265", "hevc", "avc",
        ]:
            name = re.sub(rf"\b{re.escape(kw)}\b", "", name, flags=re.IGNORECASE)
        # 清理多余分隔符和空格
        name = re.sub(r"[._\- ]+", " ", name).strip()
        return name or stem

    @staticmethod
    def _guess_year_from_filename(stem: str) -> int:
        """从文件名中提取年份"""
        match = re.search(r"\b(19\d{2}|20\d{2})\b", stem)
        if match:
            return int(match.group(1))
        return 0


# ===================================================================
# DirectoryOrganizer
# ===================================================================

class DirectoryOrganizer:
    """目录组织器

    根据策略决定媒体文件的存放目录结构。
    """

    @staticmethod
    def organize_movie_folder(
        title: str,
        year: int,
        target_dir: str,
        strategy: str = "year",
    ) -> str:
        """根据策略确定电影存放目录

        支持策略：
        - ``"year"``: {target_dir}/{year}/{title} ({year})
        - ``"first_letter"``: {target_dir}/{title[0]}/{title} ({year})
        - ``"flat"``: {target_dir}/{title} ({year})
        """
        safe_title = FileManager.safe_filename(FileManager._guess_title_from_filename(title) if title else title)
        # 如果没有 title 则直接使用传入的 title（已清理）
        folder_name = f"{safe_title} ({year})" if year else safe_title

        if strategy == "year":
            if not year:
                return str(Path(target_dir) / "Unknown" / folder_name)
            return str(Path(target_dir) / str(year) / folder_name)

        elif strategy == "first_letter":
            first_char = safe_title[0].upper() if safe_title else "#"
            if first_char.isalpha():
                return str(Path(target_dir) / first_char / folder_name)
            return str(Path(target_dir) / "#" / folder_name)

        else:  # flat
            return str(Path(target_dir) / folder_name)

    @staticmethod
    def organize_tv_folder(title: str, target_dir: str) -> str:
        """电视剧目录：{target_dir}/{title}"""
        safe_title = FileManager.safe_filename(title)
        return str(Path(target_dir) / safe_title)
