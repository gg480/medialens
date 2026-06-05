"""
MediaLens 匹配管道编排引擎

实现 5 级优先级链（Cache -> TMDB精确 -> TMDB模糊 -> LLM补充 -> 用户确认），
确保 TMDB 是主要匹配源，LLM 只做补充裁决。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

from medialens.detector.format_detector import detect_format
from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaLensConfig,
    MediaType,
    ParsedMedia,
)
from medialens.parser.parser_factory import ParserFactory
from medialens.storage.database import DatabaseManager

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 65536


class MatcherPipeline:
    """匹配管道编排器，按 5 级优先级链执行匹配。"""

    def __init__(self, tmdb_api_key: str, config: MediaLensConfig) -> None:
        self.config = config
        self.db = DatabaseManager(db_path=config.db_path)
        self.parser_factory = ParserFactory()
        self.tmdb_matcher = self._init_tmdb_matcher(tmdb_api_key, config.tmdb_language)
        self.llm_matcher = self._init_llm_matcher(config)

    # ================================================================
    # 公开方法
    # ================================================================

    def match(self, path: str) -> MatchResult:
        """
        执行完整 5 级匹配流程。

        优先级链（命中即返回，不再执行后续级别）:
        1. Cache Hit（数据库缓存）
        2. TMDB 精确匹配（confidence >= tmdb_threshold）
        3. TMDB 模糊匹配（confidence >= llm_threshold）
        4. LLM 补充裁决
        5. 返回未匹配结果

        Args:
            path: 文件或目录路径

        Returns:
            匹配结果
        """
        file_hash = self.compute_file_hash(path)

        # Level 1: RAG Cache 命中
        cached = self._try_cache_hit(file_hash, path)
        if cached is not None:
            return cached

        # Level 2-3: TMDB 匹配（精确 + 模糊）
        result = self._run_tmdb_match(path)
        if result is None:
            return self._build_no_match_result(path, file_hash=file_hash)

        # Level 2: TMDB 精确匹配
        if result.source == MatchSource.TMDB_EXACT:
            return self._finalize(result, file_hash=file_hash)

        # Level 3: TMDB 模糊匹配（置信度达到 LLM 阈值，无需 LLM 裁决）
        if result.confidence >= self.config.llm_confidence_threshold:
            result.matched = True
            result.source = MatchSource.TMDB_FUZZY
            return self._finalize(result, file_hash=file_hash)

        # Level 4: LLM 补充裁决
        llm_result = self._run_llm_match(result, path)
        if llm_result is not None:
            return self._finalize(llm_result, file_hash=file_hash)

        # Level 5: 全部失败，返回未匹配结果
        no_match = MatchResult(
            matched=False,
            media_type=result.media_type,
            tmdb_id=0,
            title=result.title,
            original_title=result.original_title,
            year=result.year,
            source=MatchSource.TMDB_FUZZY,
            confidence=result.confidence,
            candidates=result.candidates,
            season=result.season,
            episode=result.episode,
            episode_count=result.episode_count,
        )
        return self._finalize(no_match, file_hash=file_hash)

    def match_batch(self, paths: list[str]) -> list[MatchResult]:
        """
        批量匹配多个文件。

        单文件异常被捕获，确保不阻塞整个批次。

        Args:
            paths: 文件路径列表

        Returns:
            与输入顺序一致的匹配结果列表
        """
        results: list[MatchResult] = []
        for p in paths:
            try:
                result = self.match(p)
                results.append(result)
            except Exception as exc:
                logger.error("批量匹配失败 %s: %s", p, exc, exc_info=True)
                results.append(
                    MatchResult(
                        matched=False,
                        media_type=MediaType.MOVIE,
                        tmdb_id=0,
                        title=str(p),
                        original_title="",
                        source=MatchSource.TMDB_FUZZY,
                        confidence=0.0,
                        candidates=[],
                    )
                )
        return results

    def compute_file_hash(self, filepath: str) -> str:
        """
        计算文件的 SHA256 哈希（用于缓存匹配）。

        大文件只读取前 64KB 以平衡速度和准确性。
        目录（BDMV 等）用路径 + 文件名列表计算哈希。

        Args:
            filepath: 文件或目录路径

        Returns:
            SHA256 十六进制字符串
        """
        p = Path(filepath)

        if not p.exists():
            logger.warning("路径不存在，使用路径字符串计算哈希: %s", filepath)
            return hashlib.sha256(filepath.encode("utf-8")).hexdigest()

        if p.is_dir():
            hasher = hashlib.sha256()
            resolved = str(p.resolve()).encode("utf-8")
            hasher.update(resolved)
            try:
                names = sorted(entry.name for entry in p.iterdir() if entry.is_file())
                for name in names:
                    hasher.update(name.encode("utf-8"))
            except PermissionError:
                pass
            return hasher.hexdigest()

        return self._hash_file_first_chunk(p)

    def update_match_confidence(
        self,
        match_id: int,
        user_corrected_title: str,
    ) -> bool:
        """
        用户纠正匹配结果后的回调。

        更新数据库中的匹配记录，设置用户修正后的标题和来源。
        该信息可用于后续 RAG 缓存优化。

        Args:
            match_id: 数据库中的匹配记录 ID
            user_corrected_title: 用户修正后的标题

        Returns:
            是否成功更新
        """
        return self.db.update_match(
            match_id,
            {
                "matched_title": user_corrected_title,
                "match_source": MatchSource.USER_CONFIRMED.value,
                "confidence": 100.0,
            },
        )

    # ================================================================
    # 内部：子模块初始化
    # ================================================================

    def _init_tmdb_matcher(self, api_key: str, language: str) -> Any:
        """初始化 TMDB 匹配器。"""
        from medialens.matcher.tmdb_matcher import TmdbMatcher

        return TmdbMatcher(api_key=api_key, language=language)

    def _init_llm_matcher(self, config: MediaLensConfig) -> Any:
        """初始化 LLM 匹配器（仅当配置可用时）。"""
        if not config.llm_api_key and config.llm_provider != "ollama":
            logger.info("LLM API Key 未配置，跳过 LLM 匹配器初始化")
            return None

        from medialens.matcher.llm_matcher import LlmMatcher

        return LlmMatcher(
            provider=config.llm_provider,
            api_key=config.llm_api_key,
            model=config.llm_model,
            api_base=config.llm_api_base,
            db=self.db,
        )

    # ================================================================
    # 内部：匹配级别
    # ================================================================

    def _try_cache_hit(
        self,
        file_hash: str,
        path: str,
    ) -> Optional[MatchResult]:
        """
        Level 1: 尝试从数据库缓存获取匹配结果。

        Args:
            file_hash: 文件哈希
            path: 文件路径

        Returns:
            缓存命中时返回 MatchResult，否则返回 None
        """
        row = self.db.get_match_by_hash(file_hash)
        if row is None:
            return None

        logger.info("缓存命中: %s (hash=%s)", path, file_hash[:16])

        try:
            media_type = MediaType(row["media_type"]) if row.get("media_type") else MediaType.MOVIE
        except ValueError:
            media_type = MediaType.MOVIE

        return MatchResult(
            matched=True,
            media_type=media_type,
            tmdb_id=row.get("tmdb_id", 0) or 0,
            title=row.get("matched_title", "") or "",
            original_title="",
            year=row.get("matched_year") or None,
            source=MatchSource.CACHE_HIT,
            confidence=100.0,
            candidates=[],
            overview=row.get("overview", "") or "",
            poster_path=row.get("poster_path"),
            backdrop_path=row.get("backdrop_path"),
            season=row.get("parsed_season") or None,
            episode=row.get("parsed_episode") or None,
        )

    def _run_tmdb_match(self, path: str) -> Optional[MatchResult]:
        """
        Level 2-3: 执行检测 -> 解析 -> TMDB 匹配。

        Args:
            path: 文件路径

        Returns:
            MatchResult，解析/检测失败时返回 None
        """
        try:
            file_format = detect_format(path)
        except Exception as exc:
            logger.error("格式检测失败 %s: %s", path, exc)
            return None

        if file_format == FileFormat.UNKNOWN:
            logger.warning("无法识别文件格式: %s", path)
            return None

        try:
            parsed: ParsedMedia = self.parser_factory.parse(path, file_format)
        except Exception as exc:
            logger.error("文件名解析失败 %s: %s", path, exc)
            return None

        if not parsed.title:
            logger.warning("解析结果无标题，无法匹配: %s", path)
            return None

        try:
            result: MatchResult = self.tmdb_matcher.match(parsed)
        except Exception as exc:
            logger.error("TMDB 匹配失败 %s: %s", path, exc)
            return None

        # 继承解析结果中的剧集信息
        if result.season is None:
            result.season = parsed.season
        if result.episode is None:
            result.episode = parsed.episode
        if result.episode_count is None:
            result.episode_count = parsed.episode_count

        return result

    def _run_llm_match(
        self,
        tmdb_result: MatchResult,
        path: str,
    ) -> Optional[MatchResult]:
        """
        Level 4: 使用 LLM 对 TMDB 候选进行补充裁决。

        Args:
            tmdb_result: TMDB 匹配结果（含候选列表）
            path: 文件路径

        Returns:
            LLM 裁决后的 MatchResult，或 None
        """
        if self.llm_matcher is None:
            logger.info("LLM 匹配器未初始化，跳过 LLM 裁决: %s", path)
            return None

        if not tmdb_result.candidates:
            logger.info("无 TMDB 候选可供 LLM 裁决: %s", path)
            return None

        parsed = ParsedMedia(
            raw_path=Path(tmdb_result.title),
            raw_filename=tmdb_result.title,
            file_format=FileFormat.SINGLE_FILE,
            title=tmdb_result.title,
            year=tmdb_result.year,
            season=tmdb_result.season,
            episode=tmdb_result.episode,
            episode_count=tmdb_result.episode_count,
            is_episode=tmdb_result.episode is not None,
        )

        logger.info(
            "LLM 裁决中... path=%s, candidates=%d",
            path, len(tmdb_result.candidates),
        )

        try:
            selected = self.llm_matcher.verify(parsed, tmdb_result.candidates)
        except Exception as exc:
            logger.error("LLM 裁决失败 %s: %s", path, exc)
            return None

        if selected is None:
            logger.info("LLM 未选中任何候选: %s", path)
            return None

        logger.info(
            "LLM 选中候选: %s (ID=%d, score=%.1f)",
            selected.title, selected.tmdb_id, selected.match_score,
        )

        return MatchResult(
            matched=True,
            media_type=selected.media_type,
            tmdb_id=selected.tmdb_id,
            title=selected.title,
            original_title=selected.original_title,
            year=selected.year,
            source=MatchSource.LLM_VERIFIED,
            confidence=selected.match_score,
            candidates=tmdb_result.candidates,
            season=tmdb_result.season,
            episode=tmdb_result.episode,
            episode_count=tmdb_result.episode_count,
        )

    # ================================================================
    # 内部：结果收尾
    # ================================================================

    def _finalize(
        self,
        result: MatchResult,
        file_hash: str = "",
    ) -> MatchResult:
        """
        匹配结果收尾：成功匹配的结果保存到数据库。

        Args:
            result: 匹配结果
            file_hash: 文件哈希
        """
        if not result.matched:
            return result

        parsed = ParsedMedia(
            raw_path=Path(result.title),
            raw_filename=result.title,
            file_format=FileFormat.SINGLE_FILE,
            title=result.title,
            year=result.year,
            season=result.season,
            episode=result.episode,
            episode_count=result.episode_count,
            is_episode=result.episode is not None,
        )

        try:
            self.db.save_match(parsed, result, file_hash=file_hash)
        except Exception as exc:
            logger.warning("保存匹配记录失败: %s", exc)

        return result

    def _build_no_match_result(
        self,
        path: str,
        file_hash: str = "",
    ) -> MatchResult:
        """构建彻底的未匹配结果。"""
        result = MatchResult(
            matched=False,
            media_type=MediaType.MOVIE,
            tmdb_id=0,
            title=Path(path).name,
            original_title="",
            source=MatchSource.TMDB_FUZZY,
            confidence=0.0,
            candidates=[],
        )
        return self._finalize(result, file_hash=file_hash)

    # ================================================================
    # 内部：哈希工具
    # ================================================================

    @staticmethod
    def _hash_file_first_chunk(filepath: Path) -> str:
        """
        读取文件前 64KB 计算 SHA256。

        Args:
            filepath: 文件路径

        Returns:
            SHA256 十六进制字符串
        """
        hasher = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                chunk = f.read(_HASH_CHUNK_SIZE)
                hasher.update(chunk)
        except (OSError, PermissionError) as exc:
            logger.warning("读取文件哈希失败 %s: %s", filepath, exc)
            hasher.update(str(filepath).encode("utf-8"))
        return hasher.hexdigest()
