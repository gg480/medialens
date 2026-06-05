"""
TMDB 匹配模块

MediaLens 核心匹配引擎。负责任务：
1. 根据 ParsedMedia 搜索 TMDB 候选
2. 对候选进行置信度评分
3. 输出 MatchResult（TMDB_EXACT 或 TMDB_FUZZY）

TMDB 为主, LLM 为辅策略中的主匹配器。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from tmdbv3api import TMDb, Movie, TV, Search
from tmdbv3api.exceptions import TMDbException

from medialens.models import (
    MatchResult,
    MatchSource,
    MediaCandidate,
    MediaType,
    ParsedMedia,
)

logger = logging.getLogger(__name__)

# 重试配置
_MAX_RETRIES = 3
_RETRY_DELAY = 1.0  # 秒
_RETRY_BACKOFF = 2.0


class TmdbMatcher:
    """TMDB 匹配器，负责搜索 TMDB 并做置信度评分。"""

    TMDB_CONFIDENCE_THRESHOLD = 80.0

    def __init__(self, api_key: str, language: str = "zh-CN") -> None:
        """
        初始化 TMDB 客户端。

        Args:
            api_key: TMDB API Key
            language: 语言（默认 zh-CN）
        """
        self.api_key = api_key
        self.language = language

        # 初始化 tmdbv3api 客户端
        self.tmdb = TMDb()
        self.tmdb.api_key = api_key
        self.tmdb.language = language

        self.movie = Movie()
        self.tv = TV()
        self.search_api = Search()

    # ================================================================
    # 公开方法
    # ================================================================

    def search(self, parsed: ParsedMedia) -> list[MediaCandidate]:
        """
        根据解析结果搜索 TMDB，返回候选列表（最多 5 个）。

        策略：
        - 剧集（is_episode=True）→ 搜索电视剧
        - 电影（is_episode=False）→ 搜索电影
        - 对每个结果计算 match_score
        - 按分数降序排列

        Args:
            parsed: 解析后的媒体信息

        Returns:
            按 match_score 降序排列的候选列表
        """
        if not parsed.title:
            logger.warning("parsed.title 为空，跳过搜索")
            return []

        title = parsed.title
        year = parsed.year

        if parsed.is_episode:
            raw_results = self._safe_search_tvs(title, year)
            candidates = self._tv_results_to_candidates(raw_results, title, year)
        else:
            raw_results = self._safe_search_movies(title, year)
            candidates = self._movie_results_to_candidates(raw_results, title, year)

        # 按 match_score 降序排列，取前 5
        candidates.sort(key=lambda c: c.match_score, reverse=True)
        return candidates[:5]

    def match(self, parsed: ParsedMedia) -> MatchResult:
        """
        匹配文件到 TMDB 媒体。

        流程：
        1. 调用 search(parsed) 获取候选
        2. 对每个候选计算置信度评分
        3. 最优候选置信度 >= 阈值 → TMDB_EXACT
        4. 否则 → TMDB_FUZZY（含候选列表供 LLM 裁决）

        Args:
            parsed: 解析后的媒体信息

        Returns:
            匹配结果
        """
        candidates = self.search(parsed)

        if not candidates:
            return self._build_empty_result(parsed)

        best = candidates[0]

        if best.match_score >= self.TMDB_CONFIDENCE_THRESHOLD:
            logger.info(
                "TMDB 精确匹配: %s (ID=%d, score=%.1f)",
                best.title, best.tmdb_id, best.match_score,
            )
            return self._build_result(
                parsed=parsed,
                matched=True,
                media_type=best.media_type,
                tmdb_id=best.tmdb_id,
                title=best.title,
                original_title=best.original_title,
                year=best.year,
                source=MatchSource.TMDB_EXACT,
                confidence=best.match_score,
                candidates=candidates,
            )

        logger.info(
            "TMDB 模糊匹配: %s (ID=%d, score=%.1f, 阈值=%.0f)",
            best.title, best.tmdb_id, best.match_score,
            self.TMDB_CONFIDENCE_THRESHOLD,
        )
        return self._build_result(
            parsed=parsed,
            matched=False,
            media_type=best.media_type,
            tmdb_id=best.tmdb_id,
            title=best.title,
            original_title=best.original_title,
            year=best.year,
            source=MatchSource.TMDB_FUZZY,
            confidence=best.match_score,
            candidates=candidates,
        )

    def get_detail(
        self,
        tmdb_id: int,
        media_type: MediaType,
        season: Optional[int] = None,
        episode: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        获取 TMDB 详细信息（用于后续数据填充）。

        Args:
            tmdb_id: TMDB ID
            media_type: 媒体类型（MOVIE / TV）
            season: 季号（电视剧）
            episode: 集号（电视剧）

        Returns:
            包含详细信息的字典
        """
        result: dict[str, Any] = {}

        try:
            if media_type == MediaType.MOVIE:
                detail = self._safe_api_call(self.movie.details, tmdb_id)
                if detail:
                    result = self._movie_detail_to_dict(detail)
            elif media_type == MediaType.TV:
                detail = self._safe_api_call(self.tv.details, tmdb_id)
                if detail:
                    result = self._tv_detail_to_dict(detail)

                # 获取季详情
                if season is not None:
                    try:
                        season_detail = self._safe_api_call(
                            self.tv.season, tmdb_id, season
                        )
                        if season_detail:
                            result["season"] = self._season_detail_to_dict(
                                season_detail
                            )
                    except Exception as e:
                        logger.warning("获取季详情失败 (TV ID=%d, Season=%d): %s",
                                       tmdb_id, season, e)

                # 获取单集详情
                if season is not None and episode is not None:
                    try:
                        episode_detail = self._safe_api_call(
                            self.tv.episode, tmdb_id, season, episode
                        )
                        if episode_detail:
                            result["episode"] = self._episode_detail_to_dict(
                                episode_detail
                            )
                    except Exception as e:
                        logger.warning("获取剧集详情失败 (TV ID=%d, S%dE%d): %s",
                                       tmdb_id, season, episode, e)

        except Exception as e:
            logger.error("获取 TMDB 详情失败 (ID=%d): %s", tmdb_id, e, exc_info=True)

        return result

    def search_movies(
        self, title: str, year: Optional[int] = None
    ) -> list[Any]:
        """
        搜索电影（tmdbv3api 封装）。

        Args:
            title: 搜索标题
            year: 年份（可选）

        Returns:
            TMDB 返回的电影搜索结果列表
        """
        return self._safe_search_movies(title, year)

    def search_tvs(
        self, title: str, year: Optional[int] = None
    ) -> list[Any]:
        """
        搜索电视剧（tmdbv3api 封装）。

        Args:
            title: 搜索标题
            year: 年份（可选）

        Returns:
            TMDB 返回的电视剧搜索结果列表
        """
        return self._safe_search_tvs(title, year)

    # ================================================================
    # 置信度评分
    # ================================================================

    def _calculate_match_score(
        self,
        title: str,
        candidate_title: str,
        candidate_original: str,
        year: Optional[int],
        candidate_year: Optional[int],
    ) -> float:
        """
        计算单个候选的匹配分数（0-100）。

        评分规则：
        - 精确标题匹配 + 年份一致 → 95-100
        - 标题包含匹配 + 年份一致 → 80-94
        - 仅标题精确匹配（无年份）→ 60-79
        - 仅年份匹配 → 40-59
        - 模糊匹配 → 0-39

        Args:
            title: 搜索标题
            candidate_title: 候选项标题
            candidate_original: 候选项原始标题
            year: 搜索年份
            candidate_year: 候选项年份

        Returns:
            匹配分数 0-100
        """
        if not title or not candidate_title:
            return 0.0

        # 取标题和原始标题中相似度最高的
        title_sim = self._title_similarity(title, candidate_title)
        orig_sim = self._title_similarity(title, candidate_original)
        best_sim = max(title_sim, orig_sim)

        year_match = (
            year is not None
            and candidate_year is not None
            and year == candidate_year
        )

        # 精确匹配 + 年份一致
        if best_sim >= 1.0 and year_match:
            return 98.0

        # 包含匹配 + 年份一致
        if best_sim >= 0.7 and year_match:
            return 85.0

        # 仅精确标题
        if best_sim >= 1.0:
            return 70.0

        # 大小写忽略匹配 + 年份一致
        if best_sim >= 0.9 and year_match:
            return 88.0

        # 大小写忽略匹配
        if best_sim >= 0.9:
            return 65.0

        # 包含匹配
        if best_sim >= 0.7:
            return 55.0

        # 仅年份匹配
        if year_match:
            return 50.0

        # 编辑距离匹配
        if best_sim >= 0.5:
            return 30.0

        return 10.0

    def _title_similarity(self, a: str, b: str) -> float:
        """
        计算两个标题的相似度（0-1）。

        规则：
        - 精确匹配 → 1.0
        - 大小写/标点忽略匹配 → 0.9
        - 一个包含另一个（子串）→ 0.7
        - 编辑距离 < 3 → 0.5
        - 否则 → 0.0

        Args:
            a: 标题 A
            b: 标题 B

        Returns:
            相似度 0-1
        """
        if not a or not b:
            return 0.0

        # 精确匹配
        if a == b:
            return 1.0

        # 大小写/标点忽略匹配
        normalized_a = re.sub(r"[^\w\s]", "", a).lower().strip()
        normalized_b = re.sub(r"[^\w\s]", "", b).lower().strip()

        if not normalized_a or not normalized_b:
            return 0.0

        if normalized_a == normalized_b:
            return 0.9

        # 一个包含另一个
        if normalized_a in normalized_b or normalized_b in normalized_a:
            return 0.7

        # 编辑距离 < 3
        if self._levenshtein(normalized_a, normalized_b) <= 3:
            return 0.5

        return 0.0

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """计算两个字符串的莱文斯坦编辑距离。"""
        n, m = len(a), len(b)
        if n == 0:
            return m
        if m == 0:
            return n

        # 使用两行优化空间
        prev = list(range(m + 1))
        curr = [0] * (m + 1)

        for i in range(1, n + 1):
            curr[0] = i
            for j in range(1, m + 1):
                cost = 0 if a[i - 1] == b[j - 1] else 1
                curr[j] = min(
                    prev[j] + 1,        # 删除
                    curr[j - 1] + 1,    # 插入
                    prev[j - 1] + cost,  # 替换
                )
            prev, curr = curr, prev

        return prev[m]

    # ================================================================
    # API 调用（含重试和错误处理）
    # ================================================================

    def _safe_search_movies(
        self, title: str, year: Optional[int] = None
    ) -> list[Any]:
        """
        安全搜索电影（含重试和异常捕获）。

        Returns:
            搜索结果列表（出错时返回空列表）
        """
        try:
            results = self._retry_api_call(
                self.search_api.movies, title, year=year
            )
            return list(results) if results else []
        except Exception as e:
            logger.warning("搜索电影失败 title=%s year=%s: %s", title, year, e)
            return []

    def _safe_search_tvs(
        self, title: str, year: Optional[int] = None
    ) -> list[Any]:
        """
        安全搜索电视剧（含重试和异常捕获）。

        Returns:
            搜索结果列表（出错时返回空列表）
        """
        try:
            results = self._retry_api_call(
                self.search_api.tv_shows, title, year=year
            )
            return list(results) if results else []
        except Exception as e:
            logger.warning("搜索电视剧失败 title=%s year=%s: %s", title, year, e)
            return []

    def _safe_api_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """安全执行 API 调用，异常时返回 None。"""
        try:
            return self._retry_api_call(func, *args, **kwargs)
        except Exception as e:
            logger.warning("API 调用失败 %s: %s", func.__name__, e)
            return None

    def _retry_api_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """
        带重试的 API 调用。

        使用指数退避策略，最多重试 _MAX_RETRIES 次。
        """
        last_exception: Optional[Exception] = None
        delay = _RETRY_DELAY

        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except (TMDbException, ConnectionError, TimeoutError) as e:
                last_exception = e
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "API 调用重试 %d/%d: %s", attempt + 1, _MAX_RETRIES, e
                    )
                    time.sleep(delay)
                    delay *= _RETRY_BACKOFF
                else:
                    logger.error(
                        "API 调用失败（已达最大重试次数）: %s", e
                    )

        if last_exception:
            raise last_exception

        return None  # 不会执行到这里

    # ================================================================
    # TMDB 对象 → MediaCandidate 转换
    # ================================================================

    def _movie_results_to_candidates(
        self,
        results: list[Any],
        search_title: str,
        search_year: Optional[int],
    ) -> list[MediaCandidate]:
        """将 TMDB 电影搜索结果转换为 MediaCandidate 列表。"""
        candidates: list[MediaCandidate] = []

        for item in results:
            try:
                candidate = self._single_movie_to_candidate(
                    item, search_title, search_year
                )
                if candidate:
                    candidates.append(candidate)
            except Exception as e:
                logger.debug("转换电影候选失败: %s", e)
                continue

        return candidates

    def _single_movie_to_candidate(
        self,
        item: Any,
        search_title: str,
        search_year: Optional[int],
    ) -> Optional[MediaCandidate]:
        """将单个 TMDB 电影结果转换为 MediaCandidate。"""
        tmdb_id = getattr(item, "id", 0)
        if not tmdb_id:
            return None

        title = getattr(item, "title", "") or ""
        original_title = getattr(item, "original_title", "") or ""
        release_date = getattr(item, "release_date", "") or ""
        candidate_year = self._extract_year_from_date(release_date)
        overview = getattr(item, "overview", "") or ""
        poster_path = getattr(item, "poster_path", None)

        match_score = self._calculate_match_score(
            title=search_title,
            candidate_title=title,
            candidate_original=original_title,
            year=search_year,
            candidate_year=candidate_year,
        )

        return MediaCandidate(
            tmdb_id=tmdb_id,
            title=title,
            original_title=original_title,
            year=candidate_year,
            media_type=MediaType.MOVIE,
            overview=overview[:500] if overview else "",
            poster_path=poster_path,
            match_score=match_score,
        )

    def _tv_results_to_candidates(
        self,
        results: list[Any],
        search_title: str,
        search_year: Optional[int],
    ) -> list[MediaCandidate]:
        """将 TMDB 电视剧搜索结果转换为 MediaCandidate 列表。"""
        candidates: list[MediaCandidate] = []

        for item in results:
            try:
                candidate = self._single_tv_to_candidate(
                    item, search_title, search_year
                )
                if candidate:
                    candidates.append(candidate)
            except Exception as e:
                logger.debug("转换电视剧候选失败: %s", e)
                continue

        return candidates

    def _single_tv_to_candidate(
        self,
        item: Any,
        search_title: str,
        search_year: Optional[int],
    ) -> Optional[MediaCandidate]:
        """将单个 TMDB 电视剧结果转换为 MediaCandidate。"""
        tmdb_id = getattr(item, "id", 0)
        if not tmdb_id:
            return None

        title = getattr(item, "name", "") or ""
        original_title = getattr(item, "original_name", "") or ""
        first_air_date = getattr(item, "first_air_date", "") or ""
        candidate_year = self._extract_year_from_date(first_air_date)
        overview = getattr(item, "overview", "") or ""
        poster_path = getattr(item, "poster_path", None)

        match_score = self._calculate_match_score(
            title=search_title,
            candidate_title=title,
            candidate_original=original_title,
            year=search_year,
            candidate_year=candidate_year,
        )

        return MediaCandidate(
            tmdb_id=tmdb_id,
            title=title,
            original_title=original_title,
            year=candidate_year,
            media_type=MediaType.TV,
            overview=overview[:500] if overview else "",
            poster_path=poster_path,
            match_score=match_score,
        )

    # ================================================================
    # 详情对象 → dict 转换
    # ================================================================

    @staticmethod
    def _movie_detail_to_dict(detail: Any) -> dict[str, Any]:
        """将 TMDB 电影详情对象转为字典。"""
        return {
            "tmdb_id": getattr(detail, "id", 0),
            "title": getattr(detail, "title", ""),
            "original_title": getattr(detail, "original_title", ""),
            "overview": getattr(detail, "overview", ""),
            "poster_path": getattr(detail, "poster_path", None),
            "backdrop_path": getattr(detail, "backdrop_path", None),
            "release_date": getattr(detail, "release_date", ""),
            "vote_average": getattr(detail, "vote_average", 0.0),
            "genres": [
                {"id": g.id, "name": g.name}
                for g in (getattr(detail, "genres", None) or [])
            ],
            "runtime": getattr(detail, "runtime", 0),
            "tagline": getattr(detail, "tagline", ""),
            "status": getattr(detail, "status", ""),
            "imdb_id": getattr(detail, "imdb_id", None),
            "media_type": MediaType.MOVIE.value,
        }

    @staticmethod
    def _tv_detail_to_dict(detail: Any) -> dict[str, Any]:
        """将 TMDB 电视剧详情对象转为字典。"""
        seasons = []
        for s in (getattr(detail, "seasons", None) or []):
            seasons.append({
                "id": getattr(s, "id", 0),
                "season_number": getattr(s, "season_number", 0),
                "episode_count": getattr(s, "episode_count", 0),
                "air_date": getattr(s, "air_date", ""),
                "poster_path": getattr(s, "poster_path", None),
            })

        return {
            "tmdb_id": getattr(detail, "id", 0),
            "title": getattr(detail, "name", ""),
            "original_title": getattr(detail, "original_name", ""),
            "overview": getattr(detail, "overview", ""),
            "poster_path": getattr(detail, "poster_path", None),
            "backdrop_path": getattr(detail, "backdrop_path", None),
            "first_air_date": getattr(detail, "first_air_date", ""),
            "vote_average": getattr(detail, "vote_average", 0.0),
            "genres": [
                {"id": g.id, "name": g.name}
                for g in (getattr(detail, "genres", None) or [])
            ],
            "number_of_seasons": getattr(detail, "number_of_seasons", 0),
            "number_of_episodes": getattr(detail, "number_of_episodes", 0),
            "status": getattr(detail, "status", ""),
            "tagline": getattr(detail, "tagline", ""),
            "in_production": getattr(detail, "in_production", False),
            "seasons": seasons,
            "media_type": MediaType.TV.value,
        }

    @staticmethod
    def _season_detail_to_dict(detail: Any) -> dict[str, Any]:
        """将 TMDB 季详情对象转为字典。"""
        episodes = []
        for ep in (getattr(detail, "episodes", None) or []):
            episodes.append({
                "id": getattr(ep, "id", 0),
                "episode_number": getattr(ep, "episode_number", 0),
                "name": getattr(ep, "name", ""),
                "overview": getattr(ep, "overview", ""),
                "still_path": getattr(ep, "still_path", None),
                "air_date": getattr(ep, "air_date", ""),
            })

        return {
            "season_number": getattr(detail, "season_number", 0),
            "episodes": episodes,
            "episode_count": len(episodes),
        }

    @staticmethod
    def _episode_detail_to_dict(detail: Any) -> dict[str, Any]:
        """将 TMDB 剧集详情对象转为字典。"""
        return {
            "episode_number": getattr(detail, "episode_number", 0),
            "season_number": getattr(detail, "season_number", 0),
            "name": getattr(detail, "name", ""),
            "overview": getattr(detail, "overview", ""),
            "still_path": getattr(detail, "still_path", None),
            "air_date": getattr(detail, "air_date", ""),
            "vote_average": getattr(detail, "vote_average", 0.0),
            "runtime": getattr(detail, "runtime", 0),
        }

    # ================================================================
    # 结果构建
    # ================================================================

    @staticmethod
    def _build_result(
        parsed: ParsedMedia,
        matched: bool,
        media_type: MediaType,
        tmdb_id: int,
        title: str,
        original_title: str,
        year: Optional[int],
        source: MatchSource,
        confidence: float,
        candidates: list[MediaCandidate],
    ) -> MatchResult:
        """构建 MatchResult。"""
        return MatchResult(
            matched=matched,
            media_type=media_type,
            tmdb_id=tmdb_id,
            title=title,
            original_title=original_title,
            year=year,
            source=source,
            confidence=confidence,
            candidates=candidates,
            # 从解析结果继承剧集信息
            season=parsed.season,
            episode=parsed.episode,
            episode_count=parsed.episode_count,
        )

    @staticmethod
    def _build_empty_result(parsed: ParsedMedia) -> MatchResult:
        """构建空匹配结果（未找到任何候选）。"""
        return MatchResult(
            matched=False,
            media_type=MediaType.MOVIE if not parsed.is_episode else MediaType.TV,
            tmdb_id=0,
            title=parsed.title or "",
            original_title="",
            year=parsed.year,
            source=MatchSource.TMDB_FUZZY,
            confidence=0.0,
            candidates=[],
            season=parsed.season,
            episode=parsed.episode,
            episode_count=parsed.episode_count,
        )

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _extract_year_from_date(date_str: str) -> Optional[int]:
        """从日期字符串（如 '1999-03-31'）中提取年份。"""
        if not date_str:
            return None
        match = re.match(r"(\d{4})", date_str)
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2050:
                return year
        return None
