"""
RAG 缓存学习模块

增强 MediaLens 的匹配缓存能力，提供：
1. 文本相似度匹配：文件名相似但不完全相同的文件也能命中缓存
2. 用户纠正学习：用户手动纠正匹配结果后，相似文件自动受益
3. 置信度提升：对反复出现的匹配模式自动提升置信度

所有操作通过 DatabaseManager 进行，不修改现有 match_history 表结构。
"""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path
from typing import Any, Optional

from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaType,
    ParsedMedia,
)
from medialens.storage.database import DatabaseManager

logger = logging.getLogger(__name__)

# 标题相似度阈值，高于此值视为模糊匹配命中
_TITLE_SIMILARITY_THRESHOLD = 0.85


class RAGCache:
    """RAG 缓存学习引擎，增强匹配管道的缓存能力。

    提供三级缓存查询（精确路径 → 标题相似度 → 同系列模式），
    以及用户纠正回传学习和匹配建议功能。

    Args:
        db_manager: 已初始化的 DatabaseManager 实例
    """

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db = db_manager

    # ==================================================================
    # 多级缓存查询
    # ==================================================================

    def lookup(self, parsed: ParsedMedia) -> Optional[MatchResult]:
        """多级缓存查询，按优先级返回匹配结果。

        优先级链（命中即返回，不再执行后续级别）:
        1. 精确路径/哈希缓存
        2. 标题相似度缓存（difflib SequenceMatcher > 0.85）
        3. 模式缓存（同系列多文件匹配）

        Args:
            parsed: 解析后的媒体信息

        Returns:
            缓存命中时返回 MatchResult，否则返回 None
        """
        # Level 1: 精确路径匹配
        result = self._exact_path_lookup(parsed)
        if result is not None:
            return result

        # Level 2: 标题相似度匹配
        result = self._fuzzy_title_lookup(parsed)
        if result is not None:
            return result

        # Level 3: 模式缓存（同系列匹配，仅剧集启用）
        result = self._pattern_lookup(parsed)
        if result is not None:
            return result

        return None

    def _exact_path_lookup(self, parsed: ParsedMedia) -> Optional[MatchResult]:
        """Level 1: 通过文件路径精确匹配数据库记录。"""
        row = self.db.get_match_by_path(str(parsed.raw_path))
        if row is None:
            return None
        logger.info("RAG 精确路径命中: %s", parsed.raw_path)
        result = self._row_to_match_result(row, MatchSource.CACHE_HIT)
        return result

    def _fuzzy_title_lookup(self, parsed: ParsedMedia) -> Optional[MatchResult]:
        """Level 2: 通过标题相似度匹配历史记录。

        流程：
        1. 提取 parsed.title 用于对比
        2. 对剧集标题先提取系列名，避免不同集数被相同系列名干扰
        3. 查询数据库中有相同或相似标题的记录
        4. 同时比较 parsed_title 和 matched_title，取最高相似度
        5. 相似度 > 0.85 且文件格式匹配 → 命中
        """
        if not parsed.title:
            return None

        # 对剧集查询，使用系列名做模糊匹配（与模式缓存区分）
        query_title = (
            self._extract_series_name(parsed.title) if parsed.is_episode else parsed.title
        )

        all_records = self._query_all_matches()
        best_match: Optional[dict[str, Any]] = None
        best_ratio: float = 0.0

        for record in all_records:
            # 剧集记录交给 Level 3 模式缓存处理，Level 2 只做非剧集的模糊匹配
            if record.get("parsed_season") is not None or record.get("parsed_episode") is not None:
                continue

            # 同时检查 parsed_title 和 matched_title，取最高相似度
            for candidate_field in ("matched_title", "parsed_title"):
                db_title = record.get(candidate_field) or ""
                if not db_title:
                    continue

                ratio = difflib.SequenceMatcher(
                    None, query_title.lower(), db_title.lower()
                ).ratio()

                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = record

        if best_match is not None and best_ratio >= _TITLE_SIMILARITY_THRESHOLD:
            # 文件格式一致时才命中
            db_format = best_match.get("file_format")
            if db_format and db_format == parsed.file_format.value:
                logger.info(
                    "RAG 标题相似度命中: '%s' ~ '%s' (ratio=%.3f)",
                    parsed.title,
                    best_match.get("parsed_title", ""),
                    best_ratio,
                )
                return self._row_to_match_result(best_match, MatchSource.CACHE_HIT)

        return None

    def _pattern_lookup(self, parsed: ParsedMedia) -> Optional[MatchResult]:
        """Level 3: 同系列模式匹配（仅剧集）。

        对同系列多文件（如 S01E01, S01E02）：
        1. 提取系列名
        2. 查询数据库中同系列的历史匹配
        3. 如果系列名匹配且 TMDB ID 一致 → 部分命中

        Args:
            parsed: 解析后的媒体信息

        Returns:
            模式匹配成功时返回 MatchResult（保留原剧集信息），否则返回 None
        """
        # 只在剧集模式下启用模式匹配
        if not parsed.is_episode or not parsed.title:
            return None

        series_name = self._extract_series_name(parsed.title)
        if not series_name:
            return None

        all_records = self._query_all_matches()
        best_match: Optional[dict[str, Any]] = None
        best_ratio: float = 0.0

        for record in all_records:
            db_title = record.get("parsed_title") or record.get("matched_title") or ""
            if not db_title:
                continue

            db_series = self._extract_series_name(db_title)
            if not db_series:
                continue

            ratio = difflib.SequenceMatcher(
                None, series_name.lower(), db_series.lower()
            ).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = record

        if best_match is not None and best_ratio >= _TITLE_SIMILARITY_THRESHOLD:
            logger.info(
                "RAG 模式缓存命中: series='%s' ~ '%s' (ratio=%.3f)",
                series_name,
                best_match.get("parsed_title", ""),
                best_ratio,
            )
            result = self._row_to_match_result(best_match, MatchSource.CACHE_HIT)
            # 保留原始剧集信息（原文件的季/集号）
            result.season = parsed.season
            result.episode = parsed.episode
            return result

        return None

    # ==================================================================
    # 学习与纠正
    # ==================================================================

    def learn(
        self,
        parsed: ParsedMedia,
        result: MatchResult,
        user_corrected: bool = False,
    ) -> int:
        """学习匹配结果并记录到数据库。

        流程：
        - 记录到 match_history 表
        - 如果 user_corrected=True，标记该记录为高优先级（USER_CONFIRMED）

        Args:
            parsed: 解析后的媒体信息
            result: 匹配结果
            user_corrected: 是否为用户手动纠正

        Returns:
            匹配记录的自增 ID
        """
        if user_corrected:
            result_copy = MatchResult(
                matched=result.matched,
                media_type=result.media_type,
                tmdb_id=result.tmdb_id,
                title=result.title,
                original_title=result.original_title,
                year=result.year,
                source=MatchSource.USER_CONFIRMED,
                confidence=100.0,
                candidates=result.candidates,
                overview=result.overview,
                poster_path=result.poster_path,
                backdrop_path=result.backdrop_path,
                season=result.season,
                episode=result.episode,
                episode_count=result.episode_count,
            )
            result = result_copy

        match_id = self.db.save_match(parsed, result)

        if user_corrected:
            self.db.update_match(
                match_id,
                {
                    "match_source": MatchSource.USER_CONFIRMED.value,
                    "confidence": 100.0,
                },
            )

        return match_id

    def correct(self, match_id: int, corrected_result: MatchResult) -> bool:
        """用户纠正入口。

        流程：
        1. 更新数据库中的匹配记录
        2. 标记 source 为 USER_CONFIRMED
        3. 触发 learn() 重新索引

        Args:
            match_id: 数据库中的匹配记录 ID
            corrected_result: 纠正后的匹配结果

        Returns:
            是否成功更新
        """
        updated = self.db.update_match(
            match_id,
            {
                "tmdb_id": corrected_result.tmdb_id,
                "media_type": corrected_result.media_type.value,
                "matched_title": corrected_result.title,
                "matched_year": corrected_result.year,
                "match_source": MatchSource.USER_CONFIRMED.value,
                "confidence": 100.0,
            },
        )

        if not updated:
            logger.warning("纠正失败，匹配记录不存在: match_id=%d", match_id)
            return False

        # 获取更新后的记录，重建 ParsedMedia 并重新学习
        row = self.db.get_match_by_id(match_id)
        if row is None:
            return True

        parsed = ParsedMedia(
            raw_path=Path(row["file_path"]),
            raw_filename=Path(row["file_path"]).name,
            file_format=(
                FileFormat(row["file_format"])
                if row.get("file_format")
                else FileFormat.UNKNOWN
            ),
            title=row.get("parsed_title"),
            year=row.get("parsed_year"),
            season=row.get("parsed_season"),
            episode=row.get("parsed_episode"),
            is_episode=row.get("parsed_episode") is not None,
        )

        self.learn(parsed, corrected_result, user_corrected=True)
        return True

    # ==================================================================
    # 匹配建议（用于 UI 展示）
    # ==================================================================

    def get_suggestions(self, parsed: ParsedMedia) -> list[dict[str, Any]]:
        """基于历史数据提供匹配建议。

        流程：
        1. 查询相似标题的历史匹配
        2. 按相似度降序排列
        3. 返回最多 3 个建议

        Args:
            parsed: 解析后的媒体信息

        Returns:
            建议列表，每个建议包含 tmdb_id / title / year / media_type /
            confidence / similarity / match_source
        """
        if not parsed.title:
            return []

        all_records = self._query_all_matches()
        scored: list[tuple[float, dict[str, Any]]] = []

        for record in all_records:
            # 同时检查 parsed_title 和 matched_title，取最高相似度
            best_record_ratio = 0.0
            for candidate_field in ("matched_title", "parsed_title"):
                db_title = record.get(candidate_field) or ""
                if not db_title:
                    continue

                ratio = difflib.SequenceMatcher(
                    None, parsed.title.lower(), db_title.lower()
                ).ratio()

                if ratio > best_record_ratio:
                    best_record_ratio = ratio

            if best_record_ratio >= _TITLE_SIMILARITY_THRESHOLD:
                scored.append((best_record_ratio, record))

        # 按相似度降序排列
        scored.sort(key=lambda x: x[0], reverse=True)

        suggestions: list[dict[str, Any]] = []
        for ratio, record in scored[:3]:
            suggestions.append(
                {
                    "tmdb_id": record.get("tmdb_id"),
                    "title": record.get("matched_title", ""),
                    "year": record.get("matched_year"),
                    "media_type": record.get("media_type"),
                    "confidence": record.get("confidence", 0.0),
                    "similarity": round(ratio, 3),
                    "match_source": record.get("match_source"),
                }
            )

        return suggestions

    # ==================================================================
    # 缓存管理
    # ==================================================================

    def clear(self) -> bool:
        """清空 RAG 缓存（保留基础数据）。

        只删除 CACHE_HIT 来源的记录，保留 TMDB 匹配结果和用户确认数据。

        Returns:
            是否成功清空
        """
        try:
            c = self.db.conn.cursor()
            c.execute(
                "DELETE FROM match_history WHERE match_source = ?",
                (MatchSource.CACHE_HIT.value,),
            )
            self.db.conn.commit()
            deleted = c.rowcount
            logger.info("RAG 缓存已清空，删除 %d 条缓存记录", deleted)
            return True
        except Exception as exc:
            logger.error("清空 RAG 缓存失败: %s", exc)
            return False

    def get_stats(self) -> dict[str, Any]:
        """返回 RAG 缓存统计信息。

        Returns:
            total_entries: 总记录数
            exact_hits: 精确命中次数（CACHE_HIT 来源）
            fuzzy_hits: 模糊命中次数（TMDB_FUZZY 来源）
            correction_count: 用户纠正次数（USER_CONFIRMED 来源）
            top_patterns: 最常见的前10个标题模式
        """
        c = self.db.conn.cursor()

        total_entries = c.execute(
            "SELECT COUNT(*) FROM match_history"
        ).fetchone()[0]

        exact_hits = c.execute(
            "SELECT COUNT(*) FROM match_history WHERE match_source = ?",
            (MatchSource.CACHE_HIT.value,),
        ).fetchone()[0]

        fuzzy_hits = c.execute(
            "SELECT COUNT(*) FROM match_history WHERE match_source = ?",
            (MatchSource.TMDB_FUZZY.value,),
        ).fetchone()[0]

        correction_count = c.execute(
            "SELECT COUNT(*) FROM match_history WHERE match_source = ?",
            (MatchSource.USER_CONFIRMED.value,),
        ).fetchone()[0]

        # 最常见的前10个标题模式
        rows = c.execute(
            "SELECT parsed_title, COUNT(*) as cnt FROM match_history "
            "WHERE parsed_title IS NOT NULL AND parsed_title != '' "
            "GROUP BY parsed_title ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        top_patterns = [
            {"title": row["parsed_title"], "count": row["cnt"]} for row in rows
        ]

        return {
            "total_entries": total_entries,
            "exact_hits": exact_hits,
            "fuzzy_hits": fuzzy_hits,
            "correction_count": correction_count,
            "top_patterns": top_patterns,
        }

    # ==================================================================
    # 内部工具方法
    # ==================================================================

    def _query_all_matches(self) -> list[dict[str, Any]]:
        """查询所有包含有效标题的匹配记录，用于模糊匹配扫描。"""
        c = self.db.conn.cursor()
        rows = c.execute(
            "SELECT * FROM match_history "
            "WHERE parsed_title IS NOT NULL AND parsed_title != ''"
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _extract_series_name(title: str) -> str:
        """从剧集标题中提取系列名。

        例如：
        - "Game of Thrones S01E01" → "Game of Thrones"
        - "Breaking Bad S05E16" → "Breaking Bad"
        - "The Matrix" → "The Matrix"（非剧集格式，返回原文本）

        Args:
            title: 完整标题

        Returns:
            提取后的系列名
        """
        # 去掉季/集标记：S01E01, S01, S01E01-E02
        cleaned = re.sub(
            r"\s*S\d{2,4}(E\d{2,4}(-E?\d{2,4})?)?(\s*-\s*.*)?$",
            "",
            title,
            flags=re.I,
        )
        # 去掉中文季标记：第1季, 第一季
        cleaned = re.sub(r"\s*第[一二三四五六七八九十\d]+季.*$", "", cleaned)
        cleaned = cleaned.strip()
        return cleaned if cleaned else title

    @staticmethod
    def _row_to_match_result(
        row: dict[str, Any],
        source: MatchSource = MatchSource.CACHE_HIT,
    ) -> MatchResult:
        """将数据库行转换为 MatchResult 对象。

        Args:
            row: 数据库查询结果行
            source: 匹配来源，默认 CACHE_HIT

        Returns:
            构造好的 MatchResult
        """
        try:
            media_type = (
                MediaType(row["media_type"]) if row.get("media_type") else MediaType.MOVIE
            )
        except ValueError:
            media_type = MediaType.MOVIE

        return MatchResult(
            matched=True,
            media_type=media_type,
            tmdb_id=row.get("tmdb_id", 0) or 0,
            title=row.get("matched_title", "") or "",
            original_title="",
            year=row.get("matched_year") or None,
            source=source,
            confidence=row.get("confidence", 100.0) or 100.0,
            candidates=[],
            overview=row.get("overview", "") or "",
            poster_path=row.get("poster_path"),
            backdrop_path=row.get("backdrop_path"),
            season=row.get("parsed_season") or None,
            episode=row.get("parsed_episode") or None,
        )
