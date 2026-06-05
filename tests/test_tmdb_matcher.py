"""
TMDB 匹配模块单元测试

使用 unittest.mock 模拟 TMDB API 响应，避免实际网络调用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from medialens.models import (
    FileFormat,
    MatchSource,
    MediaCandidate,
    MediaType,
    ParsedMedia,
)
from tmdbv3api.exceptions import TMDbException

from medialens.matcher.tmdb_matcher import TmdbMatcher


# =============================================================
# 测试辅助：创建模拟 TMDB 结果对象
# =============================================================


def _make_mock_obj(**attrs: Any) -> MagicMock:
    """创建一个 MagicMock 对象，并设置指定属性。"""
    obj = MagicMock()
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


# 模拟 TMDB 电影搜索结果
MOCK_MOVIE_RESULTS = [
    _make_mock_obj(
        id=603,
        title="The Matrix",
        original_title="The Matrix",
        release_date="1999-03-31",
        overview="A computer hacker learns about the true nature of reality.",
        poster_path="/matrix.jpg",
        vote_average=8.2,
    ),
    _make_mock_obj(
        id=604,
        title="The Matrix Reloaded",
        original_title="The Matrix Reloaded",
        release_date="2003-05-15",
        overview="Neo and his allies race against time.",
        poster_path="/reloaded.jpg",
        vote_average=7.2,
    ),
    _make_mock_obj(
        id=605,
        title="The Matrix Revolutions",
        original_title="The Matrix Revolutions",
        release_date="2003-11-05",
        overview="The final battle between humans and machines.",
        poster_path="/revolutions.jpg",
        vote_average=6.8,
    ),
    _make_mock_obj(
        id=12345,
        title="The Matrix: Resurrections",
        original_title="The Matrix Resurrections",
        release_date="2021-12-22",
        overview="Return to the world of the Matrix.",
        poster_path="/resurrections.jpg",
        vote_average=6.0,
    ),
    # 结果超过 5 个，验证只返回前 5
    _make_mock_obj(
        id=99999,
        title="The Matrix Revisited",
        original_title="The Matrix Revisited",
        release_date="2001-11-20",
        overview="Making of documentary.",
        poster_path="/revisited.jpg",
        vote_average=7.5,
    ),
    _make_mock_obj(
        id=88888,
        title="Matrix: The Uncut Version",
        original_title="Making 'The Matrix'",
        release_date="2004-12-07",
        overview="Behind the scenes.",
        poster_path="/uncut.jpg",
        vote_average=7.0,
    ),
]

# 模拟 TMDB 电视剧搜索结果
MOCK_TV_RESULTS = [
    _make_mock_obj(
        id=1396,
        name="Breaking Bad",
        original_name="Breaking Bad",
        first_air_date="2008-01-20",
        overview="A high school chemistry teacher turned meth producer.",
        poster_path="/breakingbad.jpg",
        vote_average=8.9,
    ),
    _make_mock_obj(
        id=1397,
        name="Better Call Saul",
        original_name="Better Call Saul",
        first_air_date="2015-02-08",
        overview="The origin story of Saul Goodman.",
        poster_path="/saul.jpg",
        vote_average=8.5,
    ),
    _make_mock_obj(
        id=1398,
        name="Breaking Bad: The Movie",
        original_name="El Camino: A Breaking Bad Movie",
        first_air_date="2019-10-11",
        overview="Jesse Pinkman's escape story.",
        poster_path="/elcamino.jpg",
        vote_average=7.3,
    ),
]

# 模拟空结果列表
MOCK_EMPTY_RESULTS: list[Any] = []

# 模拟 TMDB 电影详情
MOCK_MOVIE_DETAIL = _make_mock_obj(
    id=603,
    title="The Matrix",
    original_title="The Matrix",
    overview="A computer hacker learns about the true nature of reality.",
    poster_path="/matrix.jpg",
    backdrop_path="/matrix_bg.jpg",
    release_date="1999-03-31",
    vote_average=8.2,
    genres=[
        _make_mock_obj(id=28, name="Action"),
        _make_mock_obj(id=878, name="Sci-Fi"),
    ],
    runtime=136,
    tagline="Welcome to the Real World",
    status="Released",
    imdb_id="tt0133093",
)


# =============================================================
# Fixtures
# =============================================================


@pytest.fixture
def matcher() -> TmdbMatcher:
    """创建一个 TmdbMatcher 实例（mock 模式）。"""
    return TmdbMatcher(api_key="test_key", language="zh-CN")


@pytest.fixture
def sample_movie_parsed() -> ParsedMedia:
    """电影解析示例。"""
    return ParsedMedia(
        raw_path=Path("/media/The Matrix (1999).mkv"),
        raw_filename="The Matrix (1999).mkv",
        file_format=FileFormat.SINGLE_FILE,
        title="The Matrix",
        year=1999,
        is_episode=False,
    )


@pytest.fixture
def sample_tv_parsed() -> ParsedMedia:
    """电视剧解析示例。"""
    return ParsedMedia(
        raw_path=Path("/media/Breaking Bad S01E01.mkv"),
        raw_filename="Breaking Bad S01E01.mkv",
        file_format=FileFormat.TV_EPISODE,
        title="Breaking Bad",
        year=2008,
        season=1,
        episode=1,
        is_episode=True,
    )


@pytest.fixture
def sample_movie_no_year() -> ParsedMedia:
    """无年份的电影解析。"""
    return ParsedMedia(
        raw_path=Path("/media/Some Movie.mkv"),
        raw_filename="Some Movie.mkv",
        file_format=FileFormat.SINGLE_FILE,
        title="Some Movie",
        year=None,
        is_episode=False,
    )


@pytest.fixture
def mock_search_movies() -> MagicMock:
    """模拟 search_api.movies 返回 MOCK_MOVIE_RESULTS。"""
    return MagicMock(return_value=MOCK_MOVIE_RESULTS)


@pytest.fixture
def mock_search_tvs() -> MagicMock:
    """模拟 search_api.tv_shows 返回 MOCK_TV_RESULTS。"""
    return MagicMock(return_value=MOCK_TV_RESULTS)


# =============================================================
# 搜索电影测试
# =============================================================


class TestSearchMovie:
    """测试电影搜索"""

    def test_search_movie_returns_candidates(
        self,
        matcher: TmdbMatcher,
        sample_movie_parsed: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证搜索电影返回 MediaCandidate 列表。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            candidates = matcher.search(sample_movie_parsed)

        assert len(candidates) > 0
        assert all(isinstance(c, MediaCandidate) for c in candidates)
        assert all(c.media_type == MediaType.MOVIE for c in candidates)

    def test_search_movie_top_candidate(
        self,
        matcher: TmdbMatcher,
        sample_movie_parsed: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证最优候选是 The Matrix (1999)。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            candidates = matcher.search(sample_movie_parsed)

        assert len(candidates) > 0
        best = candidates[0]
        assert best.tmdb_id == 603  # The Matrix
        assert best.match_score == 98.0  # 精确标题 + 年份一致

    def test_search_movie_limited_to_5(
        self,
        matcher: TmdbMatcher,
        sample_movie_parsed: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证结果不超过 5 个。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            candidates = matcher.search(sample_movie_parsed)

        assert len(candidates) <= 5

    def test_search_movie_sorted_by_score(
        self,
        matcher: TmdbMatcher,
        sample_movie_parsed: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证结果按 match_score 降序排列。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            candidates = matcher.search(sample_movie_parsed)

        scores = [c.match_score for c in candidates]
        assert scores == sorted(scores, reverse=True)


# =============================================================
# 搜索电视剧测试
# =============================================================


class TestSearchTV:
    """测试电视剧搜索"""

    def test_search_tv_returns_candidates(
        self,
        matcher: TmdbMatcher,
        sample_tv_parsed: ParsedMedia,
        mock_search_tvs: MagicMock,
    ) -> None:
        """验证搜索电视剧返回 MediaCandidate 列表。"""
        with patch.object(matcher.search_api, "tv_shows", mock_search_tvs):
            candidates = matcher.search(sample_tv_parsed)

        assert len(candidates) > 0
        assert all(isinstance(c, MediaCandidate) for c in candidates)
        assert all(c.media_type == MediaType.TV for c in candidates)

    def test_search_tv_top_candidate(
        self,
        matcher: TmdbMatcher,
        sample_tv_parsed: ParsedMedia,
        mock_search_tvs: MagicMock,
    ) -> None:
        """验证最优候选是 Breaking Bad (2008)。"""
        with patch.object(matcher.search_api, "tv_shows", mock_search_tvs):
            candidates = matcher.search(sample_tv_parsed)

        assert len(candidates) > 0
        best = candidates[0]
        assert best.tmdb_id == 1396  # Breaking Bad
        assert best.match_score == 98.0  # 精确标题 + 年份一致

    def test_search_tv_limited_to_5(
        self,
        matcher: TmdbMatcher,
        sample_tv_parsed: ParsedMedia,
        mock_search_tvs: MagicMock,
    ) -> None:
        """验证结果不超过 5 个。"""
        with patch.object(matcher.search_api, "tv_shows", mock_search_tvs):
            candidates = matcher.search(sample_tv_parsed)

        assert len(candidates) <= 5


# =============================================================
# 匹配（match）测试
# =============================================================


class TestMatchExactHighConfidence:
    """高置信度精确匹配测试"""

    def test_match_exact_returns_tmdb_exact(
        self,
        matcher: TmdbMatcher,
        sample_movie_parsed: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证高置信度匹配返回 TMDB_EXACT 结果。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            result = matcher.match(sample_movie_parsed)

        assert result.matched is True
        assert result.source == MatchSource.TMDB_EXACT
        assert result.tmdb_id == 603
        assert result.title == "The Matrix"
        assert result.confidence == 98.0

    def test_match_exact_has_candidates(
        self,
        matcher: TmdbMatcher,
        sample_movie_parsed: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证精确匹配结果包含候选列表。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            result = matcher.match(sample_movie_parsed)

        assert len(result.candidates) > 0
        assert all(isinstance(c, MediaCandidate) for c in result.candidates)


class TestMatchFuzzyLowConfidence:
    """低置信度模糊匹配测试"""

    def test_match_fuzzy_returns_tmdb_fuzzy(
        self,
        matcher: TmdbMatcher,
        sample_movie_no_year: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证低置信度匹配返回 TMDB_FUZZY 结果。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            result = matcher.match(sample_movie_no_year)

        assert result.matched is False
        assert result.source == MatchSource.TMDB_FUZZY

    def test_match_fuzzy_has_candidates(
        self,
        matcher: TmdbMatcher,
        sample_movie_no_year: ParsedMedia,
        mock_search_movies: MagicMock,
    ) -> None:
        """验证模糊匹配结果包含候选列表（供 LLM 裁决）。"""
        with patch.object(matcher.search_api, "movies", mock_search_movies):
            result = matcher.match(sample_movie_no_year)

        assert len(result.candidates) > 0
        assert result.confidence < matcher.TMDB_CONFIDENCE_THRESHOLD


# =============================================================
# 置信度评分测试
# =============================================================


class TestCalculateMatchScore:
    """匹配分数计算测试"""

    def test_exact_title_and_year(self, matcher: TmdbMatcher) -> None:
        """精确标题 + 年份一致 → 98.0"""
        score = matcher._calculate_match_score(
            title="The Matrix",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=1999,
        )
        assert score == 98.0

    def test_exact_title_different_year(self, matcher: TmdbMatcher) -> None:
        """精确标题 + 年份不同 → 70.0"""
        score = matcher._calculate_match_score(
            title="The Matrix",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=2003,
        )
        assert score == 70.0

    def test_exact_title_no_year(self, matcher: TmdbMatcher) -> None:
        """精确标题 + 无年份 → 70.0"""
        score = matcher._calculate_match_score(
            title="The Matrix",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=None,
            candidate_year=1999,
        )
        assert score == 70.0

    def test_case_insensitive_and_year(self, matcher: TmdbMatcher) -> None:
        """大小写忽略匹配 + 年份一致 → 85.0（落在 80-94 范围内）"""
        score = matcher._calculate_match_score(
            title="the matrix",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=1999,
        )
        assert score == 85.0

    def test_subtitle_match_and_year(self, matcher: TmdbMatcher) -> None:
        """包含匹配 + 年份一致 → 85.0"""
        score = matcher._calculate_match_score(
            title="Matrix",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=1999,
        )
        assert score == 85.0

    def test_only_year_match(self, matcher: TmdbMatcher) -> None:
        """仅年份匹配 → 50.0"""
        score = matcher._calculate_match_score(
            title="Some Unknown Movie",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=1999,
        )
        assert score == 50.0

    def test_no_match(self, matcher: TmdbMatcher) -> None:
        """完全不匹配 → 10.0"""
        score = matcher._calculate_match_score(
            title="完全无关的标题",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=2003,
        )
        assert score == 10.0

    def test_levenshtein_match(self, matcher: TmdbMatcher) -> None:
        """编辑距离 < 3 → 30.0"""
        score = matcher._calculate_match_score(
            title="Matrx",
            candidate_title="The Matrix",
            candidate_original="Mathrix",
            year=1999,
            candidate_year=2003,
        )
        assert score == 30.0

    def test_empty_title_returns_zero(self, matcher: TmdbMatcher) -> None:
        """空标题 → 0.0"""
        score = matcher._calculate_match_score(
            title="",
            candidate_title="The Matrix",
            candidate_original="The Matrix",
            year=1999,
            candidate_year=1999,
        )
        assert score == 0.0


# =============================================================
# 标题相似度测试
# =============================================================


class TestTitleSimilarity:
    """标题相似度计算测试"""

    def test_exact_match(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("The Matrix", "The Matrix") == 1.0

    def test_case_insensitive(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("the matrix", "The Matrix") == 0.9

    def test_punctuation_insensitive(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("The Matrix!", "The Matrix") == 0.9

    def test_one_contains_other(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("Matrix", "The Matrix") == 0.7

    def test_levenshtein_distance(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("Matrx", "Matrix") == 0.5

    def test_no_similarity(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("ABCDEF", "GHIJKL") == 0.0

    def test_empty_strings(self, matcher: TmdbMatcher) -> None:
        assert matcher._title_similarity("", "The Matrix") == 0.0
        assert matcher._title_similarity("The Matrix", "") == 0.0
        assert matcher._title_similarity("", "") == 0.0


# =============================================================
# 详情获取测试
# =============================================================


class TestGetDetail:
    """TMDB 详情获取测试"""

    def test_get_movie_detail(
        self, matcher: TmdbMatcher
    ) -> None:
        """验证获取电影详情。"""
        with patch.object(
            matcher.movie, "details", return_value=MOCK_MOVIE_DETAIL
        ):
            detail = matcher.get_detail(603, MediaType.MOVIE)

        assert detail["tmdb_id"] == 603
        assert detail["title"] == "The Matrix"
        assert detail["original_title"] == "The Matrix"
        assert detail["media_type"] == MediaType.MOVIE.value
        assert len(detail["genres"]) == 2
        assert detail["runtime"] == 136
        assert detail["imdb_id"] == "tt0133093"

    def test_get_tv_detail(self, matcher: TmdbMatcher) -> None:
        """验证获取电视剧详情（含季列表）。"""
        mock_season = _make_mock_obj(
            id=3624,
            season_number=1,
            episode_count=7,
            air_date="2008-01-20",
            poster_path="/s1.jpg",
        )
        mock_tv_detail = _make_mock_obj(
            id=1396,
            name="Breaking Bad",
            original_name="Breaking Bad",
            overview="A chemistry teacher turned meth producer.",
            poster_path="/bb.jpg",
            backdrop_path="/bb_bg.jpg",
            first_air_date="2008-01-20",
            vote_average=8.9,
            genres=[_make_mock_obj(id=18, name="Drama")],
            number_of_seasons=5,
            number_of_episodes=62,
            status="Ended",
            tagline="Say my name.",
            in_production=False,
            seasons=[mock_season],
        )

        with patch.object(matcher.tv, "details", return_value=mock_tv_detail):
            detail = matcher.get_detail(1396, MediaType.TV)

        assert detail["tmdb_id"] == 1396
        assert detail["title"] == "Breaking Bad"
        assert detail["media_type"] == MediaType.TV.value
        assert detail["number_of_seasons"] == 5
        assert detail["number_of_episodes"] == 62
        assert len(detail["seasons"]) == 1
        assert detail["seasons"][0]["season_number"] == 1

    def test_get_tv_detail_with_season(
        self, matcher: TmdbMatcher
    ) -> None:
        """验证获取电视剧详情含季信息。"""
        mock_season = _make_mock_obj(
            id=3624,
            season_number=1,
            episode_count=7,
            air_date="2008-01-20",
            poster_path="/s1.jpg",
        )
        mock_tv_detail = _make_mock_obj(
            id=1396,
            name="Breaking Bad",
            original_name="Breaking Bad",
            overview="",
            poster_path=None,
            backdrop_path=None,
            first_air_date="2008-01-20",
            vote_average=8.9,
            genres=[],
            number_of_seasons=5,
            number_of_episodes=62,
            status="Ended",
            tagline="",
            in_production=False,
            seasons=[mock_season],
        )

        mock_episode = _make_mock_obj(
            id=1,
            episode_number=1,
            season_number=1,
            name="Pilot",
            overview="Walter White's transformation begins.",
            still_path="/pilot.jpg",
            air_date="2008-01-20",
            vote_average=8.5,
            runtime=58,
        )
        mock_season_detail = _make_mock_obj(
            season_number=1, episodes=[mock_episode]
        )

        mock_tv = MagicMock()
        mock_tv.details.return_value = mock_tv_detail
        mock_tv.season.return_value = mock_season_detail

        with patch.object(matcher, "tv", mock_tv):
            detail = matcher.get_detail(1396, MediaType.TV, season=1)

        assert "season" in detail
        assert detail["season"]["season_number"] == 1
        assert len(detail["season"]["episodes"]) == 1

    def test_get_detail_api_error_returns_empty(
        self, matcher: TmdbMatcher
    ) -> None:
        """验证 API 异常时返回空字典。"""
        with patch.object(
            matcher.movie, "details", side_effect=ConnectionError("API timeout")
        ):
            detail = matcher.get_detail(0, MediaType.MOVIE)

        assert detail == {}


# =============================================================
# API 错误降级测试
# =============================================================


class TestApiErrorDegradation:
    """API 异常时优雅降级测试"""

    def test_api_error_returns_empty_list(
        self, matcher: TmdbMatcher, sample_movie_parsed: ParsedMedia
    ) -> None:
        """验证 API 异常时返回空列表而非崩溃。"""
        with patch.object(
            matcher.search_api,
            "movies",
            side_effect=TMDbException("API Error"),
        ):
            candidates = matcher.search(sample_movie_parsed)

        assert candidates == []

    def test_api_connection_error(
        self, matcher: TmdbMatcher, sample_movie_parsed: ParsedMedia
    ) -> None:
        """验证连接错误时返回空结果。"""
        with patch.object(
            matcher.search_api,
            "movies",
            side_effect=ConnectionError("Connection refused"),
        ):
            candidates = matcher.search(sample_movie_parsed)

        assert candidates == []

    def test_empty_parsed_title_returns_no_match(
        self, matcher: TmdbMatcher
    ) -> None:
        """验证空标题返回空候选列表。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/unknown.mkv"),
            raw_filename="unknown.mkv",
            file_format=FileFormat.UNKNOWN,
            title=None,
        )
        candidates = matcher.search(parsed)
        assert candidates == []

        result = matcher.match(parsed)
        assert result.matched is False
        assert result.confidence == 0.0
        assert result.candidates == []

    def test_search_movies_empty_result(
        self, matcher: TmdbMatcher, sample_movie_parsed: ParsedMedia
    ) -> None:
        """验证搜索无结果返回空列表。"""
        with patch.object(
            matcher.search_api, "movies", return_value=[]
        ):
            candidates = matcher.search(sample_movie_parsed)

        assert candidates == []

    def test_search_tvs_empty_result(
        self, matcher: TmdbMatcher, sample_tv_parsed: ParsedMedia
    ) -> None:
        """验证电视剧搜索无结果返回空列表。"""
        with patch.object(
            matcher.search_api, "tv_shows", return_value=[]
        ):
            candidates = matcher.search(sample_tv_parsed)

        assert candidates == []


# =============================================================
# 年份提取测试
# =============================================================


class TestExtractYearFromDate:
    """日期字符串年份提取测试"""

    def test_standard_date(self, matcher: TmdbMatcher) -> None:
        assert matcher._extract_year_from_date("1999-03-31") == 1999

    def test_only_year(self, matcher: TmdbMatcher) -> None:
        assert matcher._extract_year_from_date("2008") == 2008

    def test_empty_string(self, matcher: TmdbMatcher) -> None:
        assert matcher._extract_year_from_date("") is None

    def test_none_value(self, matcher: TmdbMatcher) -> None:
        assert matcher._extract_year_from_date(None) is None  # type: ignore[arg-type]

    def test_out_of_range_year(self, matcher: TmdbMatcher) -> None:
        assert matcher._extract_year_from_date("1899-01-01") is None


# =============================================================
# 边界情况测试
# =============================================================


class TestEdgeCases:
    """边界情况"""

    def test_match_with_tv_episode(self, matcher: TmdbMatcher) -> None:
        """验证剧集匹配走电视剧搜索路径。"""
        parsed = ParsedMedia(
            raw_path=Path("/media/Breaking Bad S01E01.mkv"),
            raw_filename="Breaking Bad S01E01.mkv",
            file_format=FileFormat.TV_EPISODE,
            title="Breaking Bad",
            year=2008,
            season=1,
            episode=1,
            is_episode=True,
        )

        with patch.object(
            matcher.search_api, "tv_shows", return_value=MOCK_TV_RESULTS
        ):
            candidates = matcher.search(parsed)

        assert len(candidates) > 0
        assert candidates[0].tmdb_id == 1396

    def test_search_movies_wrapper(self, matcher: TmdbMatcher) -> None:
        """验证 search_movies 公开方法。"""
        with patch.object(
            matcher.search_api, "movies", return_value=MOCK_MOVIE_RESULTS
        ):
            results = matcher.search_movies("The Matrix", 1999)

        assert len(results) == 6  # 原始返回数量，不截断

    def test_search_tvs_wrapper(self, matcher: TmdbMatcher) -> None:
        """验证 search_tvs 公开方法。"""
        with patch.object(
            matcher.search_api, "tv_shows", return_value=MOCK_TV_RESULTS
        ):
            results = matcher.search_tvs("Breaking Bad", 2008)

        assert len(results) == 3

    def test_match_result_inherits_episode_info(
        self, matcher: TmdbMatcher, sample_tv_parsed: ParsedMedia
    ) -> None:
        """验证 MatchResult 继承了解析结果的剧集信息。"""
        with patch.object(
            matcher.search_api, "tv_shows", return_value=MOCK_TV_RESULTS
        ):
            result = matcher.match(sample_tv_parsed)

        assert result.season == 1
        assert result.episode == 1

    def test_levenshtein_distance(self, matcher: TmdbMatcher) -> None:
        """验证莱文斯坦距离算法。"""
        assert matcher._levenshtein("", "") == 0
        assert matcher._levenshtein("abc", "") == 3
        assert matcher._levenshtein("", "abc") == 3
        assert matcher._levenshtein("kitten", "sitting") == 3
        assert matcher._levenshtein("matrix", "matrx") == 1
        assert matcher._levenshtein("The Matrix", "The Matrix") == 0
