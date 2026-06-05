"""
RAG 缓存学习模块单元测试

使用临时文件模拟 SQLite 数据库，测试 RAGCache 的所有方法。
覆盖精确匹配、模糊标题匹配、模式匹配、学习、纠正、建议、清空和统计。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from medialens.matcher.rag_cache import RAGCache
from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaType,
    ParsedMedia,
)
from medialens.storage.database import DatabaseManager


# ==================================================================
# Fixtures
# ==================================================================


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
def cache(db: DatabaseManager) -> RAGCache:
    """创建一个 RAGCache 实例。"""
    return RAGCache(db)


@pytest.fixture
def sample_parsed() -> ParsedMedia:
    """电影 ParsedMedia 示例。"""
    return ParsedMedia(
        raw_path=Path("/media/The Matrix (1999).mkv"),
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
    """TMDB 匹配结果示例。"""
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


@pytest.fixture
def episode_parsed() -> ParsedMedia:
    """剧集 ParsedMedia 示例。"""
    return ParsedMedia(
        raw_path=Path("/media/Breaking Bad S01E01.mkv"),
        raw_filename="Breaking Bad S01E01.mkv",
        file_format=FileFormat.TV_EPISODE,
        title="Breaking Bad S01E01",
        year=2008,
        season=1,
        episode=1,
        is_episode=True,
    )


@pytest.fixture
def episode_result() -> MatchResult:
    """剧集匹配结果示例。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.TV,
        tmdb_id=1396,
        title="Breaking Bad",
        original_title="Breaking Bad",
        year=2008,
        source=MatchSource.TMDB_EXACT,
        confidence=95.0,
        season=1,
        episode=1,
    )


# ==================================================================
# 测试用例
# ==================================================================


class TestLookup:
    """多级缓存查询测试"""

    def test_lookup_exact_match(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证精确路径匹配命中缓存。"""
        # 先保存一条记录
        cache.learn(sample_parsed, sample_result)

        # 使用相同路径查询
        result = cache.lookup(sample_parsed)
        assert result is not None
        assert result.matched is True
        assert result.tmdb_id == 603
        assert result.title == "The Matrix"
        assert result.source == MatchSource.CACHE_HIT

    def test_lookup_fuzzy_title_match(
        self, cache: RAGCache, sample_result: MatchResult
    ) -> None:
        """验证标题模糊匹配命中缓存（文件名相似但不完全相同）。"""
        # 保存精确记录到数据库
        original = ParsedMedia(
            raw_path=Path("/media/The Matrix (1999).mkv"),
            raw_filename="The Matrix (1999).mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="The Matrix",
            year=1999,
        )
        cache.learn(original, sample_result)

        # 用相似但不完全相同的标题查询
        similar = ParsedMedia(
            raw_path=Path("/media/The.Matrix.1999.BluRay.1080p.mkv"),
            raw_filename="The.Matrix.1999.BluRay.1080p.mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="The Matrix",
            year=1999,
        )
        result = cache.lookup(similar)
        assert result is not None
        assert result.matched is True
        assert result.tmdb_id == 603
        assert result.source == MatchSource.CACHE_HIT

    def test_lookup_no_match(self, cache: RAGCache) -> None:
        """验证无匹配记录时返回 None。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/Unknown Movie.mkv"),
            raw_filename="Unknown Movie.mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="A Completely Different Movie",
        )
        result = cache.lookup(parsed)
        assert result is None

    def test_lookup_exact_match_different_path(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证同标题但不同路径的文件通过模糊匹配命中。"""
        cache.learn(sample_parsed, sample_result)

        # 同标题但不同路径
        other_path = ParsedMedia(
            raw_path=Path("/media/other/Matrix (1999).mkv"),
            raw_filename="Matrix (1999).mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="The Matrix",
            year=1999,
        )
        result = cache.lookup(other_path)
        assert result is not None
        assert result.tmdb_id == 603

    def test_lookup_fuzzy_title_different_format_no_match(
        self, cache: RAGCache, sample_result: MatchResult
    ) -> None:
        """验证标题相似但文件格式不同时不命中。"""
        original = ParsedMedia(
            raw_path=Path("/media/The Matrix (1999).mkv"),
            raw_filename="The Matrix (1999).mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="The Matrix",
        )
        cache.learn(original, sample_result)

        # 同标题但格式不同
        bluray = ParsedMedia(
            raw_path=Path("/media/BDMV/The Matrix/index.bdmv"),
            raw_filename="index.bdmv",
            file_format=FileFormat.BLURAY_BDMV,
            title="The Matrix",
        )
        result = cache.lookup(bluray)
        # 精确路径不匹配，模糊标题匹配但因格式不一致而跳过
        assert result is None

    def test_lookup_empty_title(self, cache: RAGCache) -> None:
        """验证空标题查询返回 None。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/unknown.mkv"),
            raw_filename="unknown.mkv",
            file_format=FileFormat.UNKNOWN,
            title=None,
        )
        result = cache.lookup(parsed)
        assert result is None


class TestPatternMatching:
    """同系列模式匹配测试"""

    def test_pattern_matching_same_series(
        self, cache: RAGCache, episode_parsed: ParsedMedia, episode_result: MatchResult
    ) -> None:
        """验证同系列不同集数可通过模式匹配命中缓存，且保留原剧集信息。"""
        # 保存 S01E01 的记录
        cache.learn(episode_parsed, episode_result)

        # 查询同系列的另一集
        episode_s02 = ParsedMedia(
            raw_path=Path("/media/Breaking Bad S02E03.mkv"),
            raw_filename="Breaking Bad S02E03.mkv",
            file_format=FileFormat.TV_EPISODE,
            title="Breaking Bad S02E03",
            year=2009,
            season=2,
            episode=3,
            is_episode=True,
        )
        result = cache.lookup(episode_s02)
        assert result is not None
        assert result.matched is True
        assert result.tmdb_id == 1396
        # 验证保留原剧集信息（Level 3 模式缓存会覆盖 season/episode）
        assert result.season == 2
        assert result.episode == 3
        assert result.source == MatchSource.CACHE_HIT

    def test_pattern_matching_no_episode_no_match(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证非剧集文件不触发模式匹配。"""
        cache.learn(sample_parsed, sample_result)

        # 非剧集文件
        movie = ParsedMedia(
            raw_path=Path("/media/Inception.mkv"),
            raw_filename="Inception.mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="Inception",
            is_episode=False,
        )
        result = cache.lookup(movie)
        assert result is None

    def test_pattern_matching_different_series(
        self, cache: RAGCache, episode_parsed: ParsedMedia, episode_result: MatchResult
    ) -> None:
        """验证不同系列不命中模式缓存。"""
        cache.learn(episode_parsed, episode_result)

        # 完全不相关的剧集
        other = ParsedMedia(
            raw_path=Path("/media/Friends S01E01.mkv"),
            raw_filename="Friends S01E01.mkv",
            file_format=FileFormat.TV_EPISODE,
            title="Friends S01E01",
            year=1994,
            season=1,
            episode=1,
            is_episode=True,
        )
        result = cache.lookup(other)
        assert result is None


class TestLearn:
    """学习功能测试"""

    def test_learn_new_pattern(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证新匹配结果可学习并存入数据库。"""
        match_id = cache.learn(sample_parsed, sample_result)
        assert match_id > 0

        # 验证数据库中有记录
        row = cache.db.get_match_by_path(str(sample_parsed.raw_path))
        assert row is not None
        assert row["tmdb_id"] == 603
        assert row["matched_title"] == "The Matrix"

    def test_learn_user_corrected(
        self, cache: RAGCache, sample_parsed: ParsedMedia
    ) -> None:
        """验证用户纠正的匹配结果标记为 USER_CONFIRMED。"""
        corrected = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=604,
            title="The Matrix Reloaded",
            original_title="The Matrix Reloaded",
            year=2003,
            source=MatchSource.USER_CONFIRMED,
            confidence=100.0,
        )
        match_id = cache.learn(sample_parsed, corrected, user_corrected=True)
        assert match_id > 0

        row = cache.db.get_match_by_path(str(sample_parsed.raw_path))
        assert row is not None
        assert row["match_source"] == MatchSource.USER_CONFIRMED.value
        assert row["confidence"] == 100.0

    def test_learn_upsert_same_path(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证同路径学习两次只保留一条记录（UPSERT）。"""
        cache.learn(sample_parsed, sample_result)

        result2 = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=604,
            title="The Matrix Reloaded",
            original_title="The Matrix Reloaded",
            year=2003,
            source=MatchSource.TMDB_EXACT,
            confidence=90.0,
        )
        cache.learn(sample_parsed, result2)

        # 验证 UPSERT 后只有一条记录
        c = cache.db.conn.cursor()
        c.execute("SELECT COUNT(*) FROM match_history")
        assert c.fetchone()[0] == 1

        row = cache.db.get_match_by_path(str(sample_parsed.raw_path))
        assert row is not None
        assert row["tmdb_id"] == 604
        assert row["matched_title"] == "The Matrix Reloaded"


class TestCorrect:
    """用户纠正功能测试"""

    def test_correct_updates_database(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证用户纠正后数据库记录已更新。"""
        match_id = cache.learn(sample_parsed, sample_result)

        # 用户纠正
        corrected = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=1399,
            title="The Matrix Reloaded",
            original_title="The Matrix Reloaded",
            year=2003,
            source=MatchSource.USER_CONFIRMED,
            confidence=100.0,
        )
        success = cache.correct(match_id, corrected)
        assert success is True

        # 验证数据库已更新
        row = cache.db.get_match_by_id(match_id)
        assert row is not None
        assert row["tmdb_id"] == 1399
        assert row["matched_title"] == "The Matrix Reloaded"
        assert row["match_source"] == MatchSource.USER_CONFIRMED.value
        assert row["confidence"] == 100.0

    def test_correct_nonexistent_id(self, cache: RAGCache) -> None:
        """纠正不存在的记录返回 False。"""
        corrected = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=999,
            title="Nonexistent",
            original_title="",
            source=MatchSource.USER_CONFIRMED,
            confidence=100.0,
        )
        success = cache.correct(99999, corrected)
        assert success is False

    def test_correct_triggers_learning(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证纠正后重新学习，相似文件可命中模糊匹配。"""
        match_id = cache.learn(sample_parsed, sample_result)

        # 纠正
        corrected = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=1399,
            title="The Matrix Reloaded",
            original_title="The Matrix Reloaded",
            year=2003,
            source=MatchSource.USER_CONFIRMED,
            confidence=100.0,
        )
        cache.correct(match_id, corrected)

        # 相似文件应能命中纠正后的结果
        similar = ParsedMedia(
            raw_path=Path("/media/Matrix Reloaded (2003).mkv"),
            raw_filename="Matrix Reloaded (2003).mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="Matrix Reloaded",
        )
        result = cache.lookup(similar)
        assert result is not None
        assert result.tmdb_id == 1399
        assert result.title == "The Matrix Reloaded"


class TestGetSuggestions:
    """匹配建议功能测试"""

    def test_get_suggestions(
        self, cache: RAGCache, sample_result: MatchResult
    ) -> None:
        """验证基于历史数据的匹配建议。"""
        # 保存多条记录
        movies = [
            ("The Matrix", 1999, 603),
            ("The Matrix Reloaded", 2003, 604),
            ("The Matrix Revolutions", 2003, 605),
            ("Inception", 2010, 27205),
        ]
        for title, year, tmdb_id in movies:
            parsed = ParsedMedia(
                raw_path=Path(f"/media/{title} ({year}).mkv"),
                raw_filename=f"{title} ({year}).mkv",
                file_format=FileFormat.SINGLE_FILE,
                title=title,
                year=year,
            )
            result = MatchResult(
                matched=True,
                media_type=MediaType.MOVIE,
                tmdb_id=tmdb_id,
                title=title,
                original_title=title,
                year=year,
                source=MatchSource.TMDB_EXACT,
                confidence=95.0,
            )
            cache.learn(parsed, result)

        # 相似标题查询
        query = ParsedMedia(
            raw_path=Path("/media/The Matrix.mkv"),
            raw_filename="The Matrix.mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="The Matrix",
        )
        suggestions = cache.get_suggestions(query)
        assert len(suggestions) >= 1
        # 第一个建议应该是 The Matrix（最相似）
        assert suggestions[0]["title"] == "The Matrix"
        assert suggestions[0]["tmdb_id"] == 603

    def test_get_suggestions_max_three(
        self, cache: RAGCache, sample_result: MatchResult
    ) -> None:
        """验证最多返回 3 个建议。"""
        # 保存 5 条相似标题
        for i in range(5):
            title = f"The Matrix {i}"
            parsed = ParsedMedia(
                raw_path=Path(f"/media/{title}.mkv"),
                raw_filename=f"{title}.mkv",
                file_format=FileFormat.SINGLE_FILE,
                title=title,
            )
            result = MatchResult(
                matched=True,
                media_type=MediaType.MOVIE,
                tmdb_id=600 + i,
                title=title,
                original_title=title,
                source=MatchSource.TMDB_EXACT,
                confidence=95.0,
            )
            cache.learn(parsed, result)

        query = ParsedMedia(
            raw_path=Path("/media/The Matrix.mkv"),
            raw_filename="The Matrix.mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="The Matrix",
        )
        suggestions = cache.get_suggestions(query)
        assert len(suggestions) <= 3

    def test_get_suggestions_no_match(self, cache: RAGCache) -> None:
        """验证无匹配时返回空列表。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/unknown.mkv"),
            raw_filename="unknown.mkv",
            file_format=FileFormat.UNKNOWN,
            title="Nonexistent Movie Title",
        )
        suggestions = cache.get_suggestions(parsed)
        assert suggestions == []

    def test_get_suggestions_empty_title(self, cache: RAGCache) -> None:
        """验证空标题时返回空列表。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/unknown.mkv"),
            raw_filename="unknown.mkv",
            file_format=FileFormat.UNKNOWN,
            title=None,
        )
        suggestions = cache.get_suggestions(parsed)
        assert suggestions == []


class TestClearCache:
    """清空缓存测试"""

    def test_clear_cache(
        self, cache: RAGCache, sample_parsed: ParsedMedia, sample_result: MatchResult
    ) -> None:
        """验证清空缓存只删除 CACHE_HIT 记录，保留其他记录。"""
        # 保存一条 CACHE_HIT 记录
        cache.learn(sample_parsed, sample_result)

        # 再保存一条 TMDB_EXACT 记录
        parsed2 = ParsedMedia(
            raw_path=Path("/media/Inception.mkv"),
            raw_filename="Inception.mkv",
            file_format=FileFormat.SINGLE_FILE,
            title="Inception",
        )
        result2 = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=27205,
            title="Inception",
            original_title="Inception",
            year=2010,
            source=MatchSource.TMDB_EXACT,
            confidence=95.0,
        )
        # 直接通过 DB 保存（避免 learn 将 source 改为 CACHE_HIT）
        cache.db.save_match(parsed2, result2)

        # 清空缓存
        success = cache.clear()
        assert success is True

        # 验证 CACHE_HIT 已被删除
        c = cache.db.conn.cursor()
        cache_hits = c.execute(
            "SELECT COUNT(*) FROM match_history WHERE match_source = ?",
            (MatchSource.CACHE_HIT.value,),
        ).fetchone()[0]
        assert cache_hits == 0

        # 验证 TMDB_EXACT 记录保留
        row = cache.db.get_match_by_path(str(parsed2.raw_path))
        assert row is not None
        assert row["tmdb_id"] == 27205

    def test_clear_empty_cache(self, cache: RAGCache) -> None:
        """验证清空空数据库返回 True。"""
        success = cache.clear()
        assert success is True


class TestGetStats:
    """统计信息测试"""

    def test_get_stats(
        self, cache: RAGCache, sample_result: MatchResult
    ) -> None:
        """验证统计数据正确性。"""
        # 插入多条不同来源的记录
        sources = [
            (MatchSource.CACHE_HIT, "Movie A"),
            (MatchSource.CACHE_HIT, "Movie B"),
            (MatchSource.TMDB_FUZZY, "Movie C"),
            (MatchSource.USER_CONFIRMED, "Movie D"),
            (MatchSource.TMDB_EXACT, "Movie E"),
        ]
        for source, title in sources:
            parsed = ParsedMedia(
                raw_path=Path(f"/media/{title}.mkv"),
                raw_filename=f"{title}.mkv",
                file_format=FileFormat.SINGLE_FILE,
                title=title,
            )
            result = MatchResult(
                matched=True,
                media_type=MediaType.MOVIE,
                tmdb_id=hash(title) % 10000,
                title=title,
                original_title=title,
                source=source,
                confidence=90.0,
            )
            cache.learn(parsed, result)

        stats = cache.get_stats()
        assert stats["total_entries"] == 5
        assert stats["exact_hits"] == 2  # CACHE_HIT
        assert stats["fuzzy_hits"] == 1  # TMDB_FUZZY
        assert stats["correction_count"] == 1  # USER_CONFIRMED
        # top_patterns 应该有 5 个不同标题
        assert len(stats["top_patterns"]) == 5

    def test_get_stats_empty(self, cache: RAGCache) -> None:
        """验证空数据库的统计信息。"""
        stats = cache.get_stats()
        assert stats["total_entries"] == 0
        assert stats["exact_hits"] == 0
        assert stats["fuzzy_hits"] == 0
        assert stats["correction_count"] == 0
        assert stats["top_patterns"] == []

    def test_get_stats_top_patterns(
        self, cache: RAGCache, sample_result: MatchResult
    ) -> None:
        """验证 top_patterns 按出现次数排序。"""
        # 多次插入相同标题（使用不同路径以避免 UPSERT）
        entries = [
            (["The Matrix"] * 3, list(range(3))),
            (["Inception"] * 2, list(range(2))),
            (["Interstellar"], [0]),
        ]
        for title_list, indices in entries:
            for i in indices:
                parsed = ParsedMedia(
                    raw_path=Path(f"/media/{title_list[0]} ({1999}) v{i}.mkv"),
                    raw_filename=f"{title_list[0]} ({1999}) v{i}.mkv",
                    file_format=FileFormat.SINGLE_FILE,
                    title=title_list[0],
                )
                result = MatchResult(
                    matched=True,
                    media_type=MediaType.MOVIE,
                    tmdb_id=hash(title_list[0]) % 10000,
                    title=title_list[0],
                    original_title=title_list[0],
                    source=MatchSource.TMDB_EXACT,
                    confidence=90.0,
                )
                cache.learn(parsed, result)

        stats = cache.get_stats()
        # 第一个应该是出现次数最多的
        assert stats["top_patterns"][0]["title"] == "The Matrix"
        assert stats["top_patterns"][0]["count"] == 3
        assert stats["top_patterns"][1]["count"] == 2
        assert stats["top_patterns"][2]["count"] == 1


class TestExtractSeriesName:
    """系列名提取工具测试"""

    def test_extract_season_episode_pattern(self) -> None:
        """验证 S01E01 格式提取。"""
        assert RAGCache._extract_series_name("Breaking Bad S01E01") == "Breaking Bad"
        assert RAGCache._extract_series_name("Game of Thrones S08E06") == "Game of Thrones"

    def test_extract_season_only_pattern(self) -> None:
        """验证 S01 格式提取。"""
        assert RAGCache._extract_series_name("Friends S01") == "Friends"

    def test_extract_no_pattern(self) -> None:
        """验证非剧集格式返回原文本。"""
        assert RAGCache._extract_series_name("The Matrix") == "The Matrix"
        assert RAGCache._extract_series_name("Inception") == "Inception"

    def test_extract_chinese_pattern(self) -> None:
        """验证中文季标记提取。"""
        assert RAGCache._extract_series_name("权力的游戏 第一季") == "权力的游戏"
        assert RAGCache._extract_series_name("琅琊榜 第2季") == "琅琊榜"
