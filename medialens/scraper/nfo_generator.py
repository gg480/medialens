"""
NFO 文件生成器

将 TMDB 元数据转换为 Kodi/Emby/Jellyfin 兼容的 NFO XML 文件。
使用 TMDB Credits 接口获取演员和导演信息。

设计原则：
- 优先使用 TMDB 详情（detail）中的完整信息
- TMDB 获取失败时，使用 MatchResult 中的基础信息生成最小 NFO
- 所有外部 API 调用有超时和错误处理
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from xml.sax.saxutils import escape

from tmdbv3api import TMDb, Movie, TV
from tmdbv3api.exceptions import TMDbException

from medialens.matcher.tmdb_matcher import TmdbMatcher
from medialens.models import MatchResult, MediaType

from .image_downloader import ImageDownloader

logger = logging.getLogger(__name__)

# TMDB 图片基础 URL
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/"

# 最大重试次数
_MAX_RETRIES = 2
_RETRY_DELAY = 1.0


class NfoGenerator:
    """NFO 文件生成器。

    根据 MatchResult 和 TMDB 详情生成四种类型的 NFO 文件：
    - 电影（movie.nfo）
    - 电视剧（tvshow.nfo）
    - 季（season.nfo）
    - 单集（视频文件同名 .nfo）
    """

    def __init__(self, tmdb_api_key: str, language: str = "zh-CN") -> None:
        """初始化 NFO 生成器。

        Args:
            tmdb_api_key: TMDB API Key
            language: 语言，默认中文
        """
        # 用于获取 TMDB 详情的匹配器
        self.tmdb = TmdbMatcher(api_key=tmdb_api_key, language=language)

        # 独立的 tmdbv3api 实例，用于获取 credits（演员/导演）
        self._tmdb_client = TMDb()
        self._tmdb_client.api_key = tmdb_api_key
        self._tmdb_client.language = language
        self._movie_api = Movie()
        self._tv_api = TV()

        # 图片下载器
        self._image_downloader = ImageDownloader()

    # ================================================================
    # 公开方法
    # ================================================================

    def generate_movie_nfo(self, result: MatchResult, output_path: str) -> str:
        """生成电影 NFO 文件。

        从 TMDB 获取完整详情，包括演员和导演信息。

        Args:
            result: 匹配结果
            output_path: NFO 文件输出路径

        Returns:
            输出文件的绝对路径
        """
        detail = self.tmdb.get_detail(result.tmdb_id, MediaType.MOVIE)
        credits = self._fetch_movie_credits(result.tmdb_id) if detail else {}

        xml = self._build_movie_xml(result, detail or {}, credits)
        self._write_xml(xml, output_path)
        return os.path.abspath(output_path)

    def generate_tv_nfo(self, result: MatchResult, output_path: str) -> str:
        """生成电视剧 NFO 文件（tvshow.nfo）。

        Args:
            result: 匹配结果
            output_path: NFO 文件输出路径

        Returns:
            输出文件的绝对路径
        """
        detail = self.tmdb.get_detail(result.tmdb_id, MediaType.TV)
        credits = self._fetch_tv_credits(result.tmdb_id) if detail else {}

        xml = self._build_tv_xml(result, detail or {}, credits)
        self._write_xml(xml, output_path)
        return os.path.abspath(output_path)

    def generate_season_nfo(
        self, result: MatchResult, season_num: int, output_path: str
    ) -> str:
        """生成季 NFO 文件（season.nfo）。

        Args:
            result: 匹配结果
            season_num: 季号
            output_path: NFO 文件输出路径

        Returns:
            输出文件的绝对路径
        """
        detail = self.tmdb.get_detail(
            result.tmdb_id, MediaType.TV, season=season_num
        )
        season_detail = detail.get("season", {}) if detail else {}

        xml = self._build_season_xml(result, season_num, season_detail)
        self._write_xml(xml, output_path)
        return os.path.abspath(output_path)

    def generate_episode_nfo(self, result: MatchResult, output_path: str) -> str:
        """生成单集 NFO 文件（video_file.nfo）。

        需要 result 中包含 season 和 episode 信息。

        Args:
            result: 匹配结果
            output_path: NFO 文件输出路径

        Returns:
            输出文件的绝对路径
        """
        detail = self.tmdb.get_detail(
            result.tmdb_id,
            MediaType.TV,
            season=result.season,
            episode=result.episode,
        )
        episode_detail = detail.get("episode", {}) if detail else {}

        xml = self._build_episode_xml(result, episode_detail)
        self._write_xml(xml, output_path)
        return os.path.abspath(output_path)

    def get_image_urls(self, result: MatchResult) -> dict[str, str]:
        """返回 TMDB 图片的远程 URL。

        Args:
            result: 匹配结果

        Returns:
            URL 字典:
            {
                "poster": "https://image.tmdb.org/t/p/original/poster.jpg",
                "backdrop": "https://image.tmdb.org/t/p/original/backdrop.jpg",
                "poster_small": "https://image.tmdb.org/t/p/w500/poster.jpg",
            }
        """
        urls: dict[str, str] = {}
        poster = result.poster_path
        backdrop = result.backdrop_path

        # 如果 MatchResult 中没有，尝试从 TMDB 详情获取
        if not poster or not backdrop:
            detail = self.tmdb.get_detail(result.tmdb_id, result.media_type)
            if detail:
                poster = poster or detail.get("poster_path")
                backdrop = backdrop or detail.get("backdrop_path")

        if poster:
            urls["poster"] = f"{_TMDB_IMAGE_BASE}original{poster}"
            urls["poster_small"] = f"{_TMDB_IMAGE_BASE}w500{poster}"

        if backdrop:
            urls["backdrop"] = f"{_TMDB_IMAGE_BASE}original{backdrop}"

        return urls

    def download_images(
        self, result: MatchResult, output_dir: str
    ) -> dict[str, str]:
        """下载 TMDB 图片到本地。

        下载文件：
        - poster.jpg → {output_dir}/poster.jpg
        - backdrop.jpg → {output_dir}/backdrop.jpg（别名: fanart.jpg）

        Args:
            result: 匹配结果
            output_dir: 图片输出目录

        Returns:
            已下载的本地路径字典:
            {
                "poster": "/path/to/poster.jpg",
                "backdrop": "/path/to/backdrop.jpg",
            }
        """
        os.makedirs(output_dir, exist_ok=True)
        urls = self.get_image_urls(result)
        downloaded: dict[str, str] = {}

        if "poster" in urls:
            poster_path = os.path.join(output_dir, "poster.jpg")
            try:
                self._image_downloader.download(urls["poster"], poster_path)
                downloaded["poster"] = poster_path
            except Exception as e:
                logger.warning("下载海报失败: %s", e)

        if "backdrop" in urls:
            backdrop_path = os.path.join(output_dir, "backdrop.jpg")
            try:
                self._image_downloader.download(urls["backdrop"], backdrop_path)
                downloaded["backdrop"] = backdrop_path
                # Kodi 兼容：backdrop 的别名是 fanart.jpg
                fanart_path = os.path.join(output_dir, "fanart.jpg")
                self._image_downloader.download(urls["backdrop"], fanart_path)
            except Exception as e:
                logger.warning("下载背景图失败: %s", e)

        return downloaded

    # ================================================================
    # Credits API 调用
    # ================================================================

    def _fetch_movie_credits(self, tmdb_id: int) -> dict[str, Any]:
        """获取电影演职员信息（演员和导演）。

        Args:
            tmdb_id: TMDB 电影 ID

        Returns:
            {"cast": [...], "directors": [...]}
        """
        try:
            credits = self._retry_api_call(self._movie_api.credits, tmdb_id)
            return self._parse_credits(credits)
        except Exception as e:
            logger.warning("获取电影演职员失败 (ID=%d): %s", tmdb_id, e)
            return {"cast": [], "directors": []}

    def _fetch_tv_credits(self, tmdb_id: int) -> dict[str, Any]:
        """获取电视剧演职员信息。

        Args:
            tmdb_id: TMDB 电视剧 ID

        Returns:
            {"cast": [...], "directors": [...]}
        """
        try:
            credits = self._retry_api_call(self._tv_api.credits, tmdb_id)
            return self._parse_credits(credits)
        except Exception as e:
            logger.warning("获取电视剧演职员失败 (ID=%d): %s", tmdb_id, e)
            return {"cast": [], "directors": []}

    @staticmethod
    def _parse_credits(credits_obj: Any) -> dict[str, Any]:
        """解析 tmdbv3api 返回的 credits 对象。

        Args:
            credits_obj: tmdbv3api 返回的 credits 对象

        Returns:
            解析后的演员和导演信息
        """
        cast_list: list[dict[str, str]] = []
        directors: list[str] = []

        # 解析演员
        for actor in getattr(credits_obj, "cast", None) or []:
            name = getattr(actor, "name", "") or ""
            if not name:
                continue
            profile_path = getattr(actor, "profile_path", None)
            thumb = ""
            if profile_path:
                thumb = f"{_TMDB_IMAGE_BASE}w185{profile_path}"
            cast_list.append({
                "name": name,
                "role": getattr(actor, "character", "") or "",
                "thumb": thumb,
            })

        # 解析导演
        for crew in getattr(credits_obj, "crew", None) or []:
            job = getattr(crew, "job", "") or ""
            if job == "Director":
                name = getattr(crew, "name", "") or ""
                if name:
                    directors.append(name)

        return {"cast": cast_list, "directors": directors}

    # ================================================================
    # XML 构建
    # ================================================================

    def _build_movie_xml(
        self,
        result: MatchResult,
        detail: dict[str, Any],
        credits: dict[str, Any],
    ) -> str:
        """构建电影 NFO XML 字符串。"""
        title = detail.get("title", "") or result.title
        original_title = (
            detail.get("original_title", "") or result.original_title
        )
        overview = detail.get("overview", "") or result.overview
        tagline = detail.get("tagline", "")

        lines: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            "<movie>\n",
        ]

        lines.append(self._tag("title", title))
        lines.append(self._tag("originaltitle", original_title))
        if result.year:
            lines.append(
                self._tag("sorttitle", f"{title} ({result.year})")
            )
        else:
            lines.append(self._tag("sorttitle", title))

        if result.year:
            lines.append(self._tag("year", result.year))

        release_date = detail.get("release_date", "")
        if release_date:
            lines.append(self._tag("premiered", release_date))
        elif result.year:
            lines.append(self._tag("premiered", str(result.year)))

        lines.append(self._tag("tmdbid", result.tmdb_id))
        lines.append(
            f'  <uniqueid type="tmdb" default="true">'
            f"{result.tmdb_id}</uniqueid>\n"
        )

        if overview:
            lines.append(self._cdata_tag("plot", overview))
            # outline = 简介前 200 字摘要
            outline = overview[:200]
            if len(overview) > 200:
                outline += "…"
            lines.append(self._cdata_tag("outline", outline))

        # 标签行（tagline × 1）
        if tagline:
            lines.append(self._cdata_tag("tagline", tagline))

        # 类型（多值）
        for genre in detail.get("genres", []):
            genre_name = ""
            if isinstance(genre, dict):
                genre_name = genre.get("name", "")
            else:
                genre_name = str(genre)
            if genre_name:
                lines.append(self._tag("genre", genre_name))

        # 评分
        rating = detail.get("vote_average", 0) or 0
        if rating:
            lines.append(self._tag("rating", round(float(rating), 1)))

        # 片长
        runtime = detail.get("runtime", 0) or 0
        if runtime:
            lines.append(self._tag("runtime", int(runtime)))

        # 导演
        for director in credits.get("directors", []):
            if director:
                lines.append(self._tag("director", director))

        # 演员
        for actor in credits.get("cast", []):
            if not actor.get("name"):
                continue
            lines.append("  <actor>\n")
            lines.append(self._tag("name", actor["name"]))
            if actor.get("role"):
                lines.append(self._tag("role", actor["role"]))
            if actor.get("thumb"):
                lines.append(self._tag("thumb", actor["thumb"]))
            lines.append("  </actor>\n")

        lines.append("</movie>\n")
        return "".join(lines)

    def _build_tv_xml(
        self,
        result: MatchResult,
        detail: dict[str, Any],
        credits: dict[str, Any],
    ) -> str:
        """构建电视剧 NFO XML 字符串。"""
        title = detail.get("title", "") or result.title
        original_title = (
            detail.get("original_title", "") or result.original_title
        )
        overview = detail.get("overview", "") or result.overview
        tagline = detail.get("tagline", "")

        lines: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            "<tvshow>\n",
        ]

        lines.append(self._tag("title", title))
        lines.append(self._tag("originaltitle", original_title))
        if result.year:
            lines.append(
                self._tag("sorttitle", f"{title} ({result.year})")
            )
        else:
            lines.append(self._tag("sorttitle", title))

        if result.year:
            lines.append(self._tag("year", result.year))

        first_air_date = detail.get("first_air_date", "")
        if first_air_date:
            lines.append(self._tag("premiered", first_air_date))
        elif result.year:
            lines.append(self._tag("premiered", str(result.year)))

        lines.append(self._tag("tmdbid", result.tmdb_id))
        lines.append(
            f'  <uniqueid type="tmdb" default="true">'
            f"{result.tmdb_id}</uniqueid>\n"
        )

        # Kodi 约定：tvshow.nfo 需要 season="-1" episode="-1"
        lines.append('  <season>-1</season>\n')
        lines.append('  <episode>-1</episode>\n')

        if overview:
            lines.append(self._cdata_tag("plot", overview))
            outline = overview[:200]
            if len(overview) > 200:
                outline += "…"
            lines.append(self._cdata_tag("outline", outline))

        if tagline:
            lines.append(self._cdata_tag("tagline", tagline))

        for genre in detail.get("genres", []):
            genre_name = ""
            if isinstance(genre, dict):
                genre_name = genre.get("name", "")
            else:
                genre_name = str(genre)
            if genre_name:
                lines.append(self._tag("genre", genre_name))

        rating = detail.get("vote_average", 0) or 0
        if rating:
            lines.append(self._tag("rating", round(float(rating), 1)))

        # 总季数和总集数
        num_seasons = detail.get("number_of_seasons", 0) or 0
        num_episodes = detail.get("number_of_episodes", 0) or 0
        if num_seasons:
            lines.append(self._tag("seasonnumber", num_seasons))
            lines.append(self._tag("totalseasons", num_seasons))
        if num_episodes:
            lines.append(self._tag("totalepisodes", num_episodes))

        # 状态
        status = detail.get("status", "")
        if status:
            lines.append(self._tag("status", status))

        # 导演和演员
        for director in credits.get("directors", []):
            if director:
                lines.append(self._tag("director", director))

        for actor in credits.get("cast", []):
            if not actor.get("name"):
                continue
            lines.append("  <actor>\n")
            lines.append(self._tag("name", actor["name"]))
            if actor.get("role"):
                lines.append(self._tag("role", actor["role"]))
            if actor.get("thumb"):
                lines.append(self._tag("thumb", actor["thumb"]))
            lines.append("  </actor>\n")

        lines.append("</tvshow>\n")
        return "".join(lines)

    def _build_season_xml(
        self,
        result: MatchResult,
        season_num: int,
        season_detail: dict[str, Any],
    ) -> str:
        """构建季 NFO XML 字符串。"""
        # 获取电视剧详情，用于剧集总览 fallback
        tv_detail = self.tmdb.get_detail(result.tmdb_id, MediaType.TV)
        tv_overview = (
            tv_detail.get("overview", "") if tv_detail else result.overview
        )

        lines: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            "<season>\n",
        ]

        lines.append(self._tag("seasonnumber", season_num))
        lines.append(self._tag("title", f"Season {season_num}"))

        plot = season_detail.get("overview", "") or tv_overview
        if plot:
            lines.append(self._cdata_tag("plot", plot))

        # 尝试从季详情或电视剧详情中获取首播日期
        air_date = season_detail.get("air_date", "")
        if air_date:
            lines.append(self._tag("premiered", air_date))

        # 从首播日期或结果中提取年份
        if air_date:
            year = air_date[:4] if len(air_date) >= 4 else ""
            if year:
                lines.append(self._tag("year", year))
        elif result.year:
            lines.append(self._tag("year", result.year))

        lines.append(self._tag("tmdbid", result.tmdb_id))

        lines.append("</season>\n")
        return "".join(lines)

    def _build_episode_xml(
        self,
        result: MatchResult,
        episode_detail: dict[str, Any],
    ) -> str:
        """构建单集 NFO XML 字符串。"""
        ep_name = episode_detail.get("name", "") or ""
        ep_overview = episode_detail.get("overview", "") or result.overview
        ep_air_date = episode_detail.get("air_date", "")
        ep_still = episode_detail.get("still_path", "")

        lines: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            "<episodedetails>\n",
        ]

        # 集标题：优先使用 TMDB 的集名称，其次使用 MatchResult 的 title
        lines.append(self._tag("title", ep_name or result.title))
        # showtitle = 电视剧名
        lines.append(self._tag("showtitle", result.title))

        season = (
            episode_detail.get("season_number", None)
            or result.season
            or 0
        )
        episode = (
            episode_detail.get("episode_number", None)
            or result.episode
            or 0
        )
        lines.append(self._tag("season", season))
        lines.append(self._tag("episode", episode))

        if ep_overview:
            lines.append(self._cdata_tag("plot", ep_overview))

        if ep_air_date:
            lines.append(self._tag("premiered", ep_air_date))
            if len(ep_air_date) >= 4:
                lines.append(self._tag("year", ep_air_date[:4]))
        elif result.year:
            lines.append(self._tag("year", result.year))

        lines.append(self._tag("tmdbid", result.tmdb_id))
        lines.append(
            f'  <uniqueid type="tmdb" default="true">'
            f"{result.tmdb_id}</uniqueid>\n"
        )

        if ep_still:
            lines.append(
                self._tag(
                    "thumb",
                    f"{_TMDB_IMAGE_BASE}original{ep_still}",
                )
            )

        lines.append("</episodedetails>\n")
        return "".join(lines)

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _write_xml(xml: str, output_path: str) -> None:
        """将 XML 字符串写入文件。

        Args:
            xml: XML 字符串
            output_path: 输出路径
        """
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(xml)

    @staticmethod
    def _tag(name: str, value: Any) -> str:
        """生成 XML 标签：<name>转义后的 value</name>。

        如果值为空或 None，返回空字符串。
        """
        if value is None:
            return ""
        str_val = str(value)
        if not str_val:
            return ""
        return f"  <{name}>{escape(str_val)}</{name}>\n"

    @staticmethod
    def _cdata_tag(name: str, value: Any) -> str:
        """生成 CDATA 包裹的 XML 标签。

        适用于 plot/outline 等可能包含特殊字符的字段。
        """
        if not value:
            return ""
        str_val = str(value)
        if not str_val:
            return ""
        # CDATA 内不能包含 ]]>，如有则转义
        safe_val = str_val.replace("]]>", "]]]]><![CDATA[>")
        return f"  <{name}><![CDATA[{safe_val}]]></{name}>\n"

    @staticmethod
    def _retry_api_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        """带重试的 TMDB API 调用。

        使用固定延迟，最多重试 _MAX_RETRIES 次。
        """
        import time

        last_exception: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except (TMDbException, ConnectionError, TimeoutError) as e:
                last_exception = e
                if attempt < _MAX_RETRIES - 1:
                    logger.warning(
                        "Credits API 重试 %d/%d: %s",
                        attempt + 1,
                        _MAX_RETRIES,
                        e,
                    )
                    time.sleep(_RETRY_DELAY)
                else:
                    logger.error(
                        "Credits API 失败（已达最大重试次数）: %s", e
                    )

        if last_exception:
            raise last_exception
        return None
