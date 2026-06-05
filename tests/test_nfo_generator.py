"""
NFO 生成模块单元测试

覆盖：
- 四种 NFO 类型生成（movie/tv/season/episode）
- XML 内容正确性验证
- TMDB 详情获取失败时的最小 NFO 降级
- 图片 URL 生成
- 图片下载
- XML 格式有效性
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest

from medialens.models import MatchResult, MatchSource, MediaType
from medialens.scraper.nfo_generator import NfoGenerator
from medialens.scraper.image_downloader import ImageDownloader, ImageDownloadError


# =============================================================
# 模拟数据
# =============================================================

MOCK_MOVIE_DETAIL: dict[str, Any] = {
    "tmdb_id": 9470,
    "title": "功夫",
    "original_title": "Kung Fu Hustle",
    "overview": "1940年代的上海，小混混阿星梦想加入势力强大的斧头帮。"
    "在经历了一系列荒诞事件后，他发现了自己真正的功夫天赋。",
    "poster_path": "/kungfu_poster.jpg",
    "backdrop_path": "/kungfu_backdrop.jpg",
    "release_date": "2004-12-23",
    "vote_average": 7.5,
    "genres": [
        {"id": 28, "name": "动作"},
        {"id": 35, "name": "喜剧"},
    ],
    "runtime": 99,
    "tagline": "一个关于功夫的传奇故事",
    "status": "Released",
    "imdb_id": "tt0373074",
    "media_type": "movie",
}

MOCK_MOVIE_CREDITS: dict[str, Any] = {
    "cast": [
        {"name": "周星驰", "role": "星", "thumb": "/actor_star.jpg"},
        {"name": "元华", "role": "包租公", "thumb": "/actor_yuan.jpg"},
    ],
    "directors": ["周星驰"],
}

MOCK_TV_DETAIL: dict[str, Any] = {
    "tmdb_id": 1396,
    "title": "Breaking Bad",
    "original_title": "Breaking Bad",
    "overview": "A high school chemistry teacher turned meth producer.",
    "poster_path": "/bb_poster.jpg",
    "backdrop_path": "/bb_backdrop.jpg",
    "first_air_date": "2008-01-20",
    "vote_average": 8.9,
    "genres": [
        {"id": 18, "name": "Drama"},
    ],
    "number_of_seasons": 5,
    "number_of_episodes": 62,
    "status": "Ended",
    "tagline": "Say my name.",
    "media_type": "tv",
}

MOCK_TV_CREDITS: dict[str, Any] = {
    "cast": [
        {"name": "Bryan Cranston", "role": "Walter White", "thumb": "/bb_bryan.jpg"},
    ],
    "directors": ["Vince Gilligan"],
}

MOCK_SEASON_DETAIL: dict[str, Any] = {
    "season_number": 1,
    "episode_count": 7,
    "air_date": "2008-01-20",
    "overview": "The first season of Breaking Bad.",
    "episodes": [
        {
            "id": 1,
            "episode_number": 1,
            "name": "Pilot",
            "overview": "Walter White turns to meth production.",
            "still_path": "/still_pilot.jpg",
            "air_date": "2008-01-20",
        },
    ],
}

MOCK_EPISODE_DETAIL: dict[str, Any] = {
    "episode_number": 1,
    "season_number": 1,
    "name": "Pilot",
    "overview": "Walter White, a high school chemistry teacher.",
    "still_path": "/still_pilot.jpg",
    "air_date": "2008-01-20",
    "vote_average": 8.5,
    "runtime": 58,
}


# =============================================================
# Fixtures
# =============================================================


@pytest.fixture
def generator() -> NfoGenerator:
    """创建一个 NfoGenerator 实例（mock 模式）。"""
    return NfoGenerator(tmdb_api_key="test_key", language="zh-CN")


@pytest.fixture
def movie_result() -> MatchResult:
    """电影匹配结果。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.MOVIE,
        tmdb_id=9470,
        title="功夫",
        original_title="Kung Fu Hustle",
        year=2004,
        source=MatchSource.TMDB_EXACT,
        confidence=98.0,
        overview="1940年代的上海，小混混阿星梦想加入斧头帮。",
        poster_path="/kungfu_poster.jpg",
        backdrop_path="/kungfu_backdrop.jpg",
    )


@pytest.fixture
def tv_result() -> MatchResult:
    """电视剧匹配结果。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.TV,
        tmdb_id=1396,
        title="Breaking Bad",
        original_title="Breaking Bad",
        year=2008,
        source=MatchSource.TMDB_EXACT,
        confidence=98.0,
        overview="A high school chemistry teacher turned meth producer.",
        poster_path="/bb_poster.jpg",
        backdrop_path="/bb_backdrop.jpg",
    )


@pytest.fixture
def season_result() -> MatchResult:
    """季匹配结果。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.TV,
        tmdb_id=1396,
        title="Breaking Bad",
        original_title="Breaking Bad",
        year=2008,
        source=MatchSource.TMDB_EXACT,
        confidence=98.0,
        overview="A high school chemistry teacher turned meth producer.",
        season=1,
    )


@pytest.fixture
def episode_result() -> MatchResult:
    """单集匹配结果。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.TV,
        tmdb_id=1396,
        title="Breaking Bad",
        original_title="Breaking Bad",
        year=2008,
        source=MatchSource.TMDB_EXACT,
        confidence=98.0,
        overview="Walter White, a high school chemistry teacher.",
        season=1,
        episode=1,
    )


@pytest.fixture
def minimal_result() -> MatchResult:
    """最小匹配结果（无详细信息的降级场景）。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.MOVIE,
        tmdb_id=99999,
        title="Unknown Movie",
        original_title="Unknown Original",
        year=2020,
        source=MatchSource.TMDB_FUZZY,
        confidence=60.0,
    )


@pytest.fixture
def temp_dir() -> Generator[str, None, None]:
    """临时目录，测试完成后自动清理。"""
    with tempfile.TemporaryDirectory() as td:
        yield td


# =============================================================
# Mock helpers
# =============================================================


def _make_credits_mock(cast_list: list[dict], crew_list: list[dict]) -> MagicMock:
    """创建模拟的 tmdbv3api credits 对象。"""
    mock_credits = MagicMock()

    cast_mocks = []
    for actor in cast_list:
        a = MagicMock()
        a.name = actor["name"]
        a.character = actor.get("role", "")
        a.profile_path = actor.get("thumb", None)
        cast_mocks.append(a)

    crew_mocks = []
    for member in crew_list:
        c = MagicMock()
        c.name = member["name"]
        c.job = member["job"]
        crew_mocks.append(c)

    mock_credits.cast = cast_mocks
    mock_credits.crew = crew_mocks
    return mock_credits


# =============================================================
# 测试：电影 NFO 生成
# =============================================================


class TestGenerateMovieNfo:
    """电影 NFO 生成测试。"""

    def test_generate_movie_nfo(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证电影 NFO 文件被正常创建。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            result = generator.generate_movie_nfo(movie_result, output)

        assert os.path.exists(output)
        assert result == os.path.abspath(output)

    def test_nfo_content_contains_title_and_year(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 NFO 内容包含标题和年份。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<title>功夫</title>" in content
        assert "<year>2004</year>" in content
        assert "<originaltitle>Kung Fu Hustle</originaltitle>" in content
        assert "<sorttitle>功夫 (2004)</sorttitle>" in content

    def test_movie_xml_has_tmdbid_and_uniqueid(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 NFO 包含正确的 tmdbid 和 uniqueid。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<tmdbid>9470</tmdbid>" in content
        assert '<uniqueid type="tmdb" default="true">9470</uniqueid>' in content

    def test_movie_xml_has_plot_cdata(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 plot 使用 CDATA 包裹。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<![CDATA[" in content
        assert "功夫天赋" in content

    def test_movie_xml_has_genres(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 NFO 包含类型标签（多值）。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<genre>动作</genre>" in content
        assert "<genre>喜剧</genre>" in content

    def test_movie_xml_has_actors_and_directors(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 NFO 包含演员和导演信息。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<director>周星驰</director>" in content
        assert "<name>周星驰</name>" in content
        assert "<role>星</role>" in content
        assert "<name>元华</name>" in content
        assert "<role>包租公</role>" in content

    def test_movie_xml_has_rating_and_runtime(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 NFO 包含评分和片长。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<rating>7.5</rating>" in content or "<rating>7.5" in content
        assert "<runtime>99</runtime>" in content

    def test_movie_xml_has_release_date(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 NFO 包含上映日期。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<premiered>2004-12-23</premiered>" in content

    def test_movie_xml_valid_format(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证生成的 XML 格式基本有效（根元素为 movie）。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "<movie>" in content
        assert "</movie>" in content

    def test_nfo_minimal_no_tmdb_detail(
        self,
        generator: NfoGenerator,
        minimal_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 TMDB 详情获取失败时，使用基础信息生成最小 NFO。"""
        output = os.path.join(temp_dir, "minimal.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value={}
            ),
            patch.object(
                generator._movie_api, "credits",
                side_effect=Exception("API Error"),
            ),
        ):
            generator.generate_movie_nfo(minimal_result, output)

        content = Path(output).read_text(encoding="utf-8")
        # 最小 NFO 应包含 MatchResult 中的基础信息
        assert "<title>Unknown Movie</title>" in content
        assert "<year>2020</year>" in content
        assert "<tmdbid>99999</tmdbid>" in content
        # 没有详细信息的字段不应出现
        assert "<genre>" not in content
        assert "<director>" not in content
        assert "<actor>" not in content


# =============================================================
# 测试：电视剧 NFO 生成
# =============================================================


class TestGenerateTvNfo:
    """电视剧 NFO 生成测试。"""

    def test_generate_tv_nfo(
        self,
        generator: NfoGenerator,
        tv_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证电视剧 NFO 文件被正常创建。"""
        output = os.path.join(temp_dir, "tvshow.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_TV_DETAIL
            ),
            patch.object(
                generator._tv_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_TV_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_TV_CREDITS["directors"]],
                ),
            ),
        ):
            result = generator.generate_tv_nfo(tv_result, output)

        assert os.path.exists(output)
        assert result == os.path.abspath(output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<tvshow>" in content
        assert "</tvshow>" in content
        assert "<title>Breaking Bad</title>" in content
        assert "<year>2008</year>" in content

    def test_tv_nfo_has_season_episode_minus_one(
        self,
        generator: NfoGenerator,
        tv_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 tvshow.nfo 包含 season=\"-1\" 和 episode=\"-1\"。"""
        output = os.path.join(temp_dir, "tvshow.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_TV_DETAIL
            ),
            patch.object(
                generator._tv_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_TV_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_TV_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_tv_nfo(tv_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<season>-1</season>" in content
        assert "<episode>-1</episode>" in content

    def test_tv_nfo_has_total_seasons_and_episodes(
        self,
        generator: NfoGenerator,
        tv_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证 tvshow.nfo 包含总季数和总集数。"""
        output = os.path.join(temp_dir, "tvshow.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_TV_DETAIL
            ),
            patch.object(
                generator._tv_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_TV_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_TV_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_tv_nfo(tv_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<totalseasons>5</totalseasons>" in content
        assert "<seasonnumber>5</seasonnumber>" in content


# =============================================================
# 测试：季 NFO 生成
# =============================================================


class TestGenerateSeasonNfo:
    """季 NFO 生成测试。"""

    def test_generate_season_nfo(
        self,
        generator: NfoGenerator,
        season_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证季 NFO 文件被正常创建。"""
        output = os.path.join(temp_dir, "season.nfo")

        season_detail = {
            "season": MOCK_SEASON_DETAIL,
        }

        with patch.object(
            generator.tmdb,
            "get_detail",
            return_value={**MOCK_TV_DETAIL, **season_detail},
        ):
            result = generator.generate_season_nfo(season_result, 1, output)

        assert os.path.exists(output)
        assert result == os.path.abspath(output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<season>" in content
        assert "</season>" in content
        assert "<seasonnumber>1</seasonnumber>" in content
        assert "<title>Season 1</title>" in content

    def test_season_nfo_has_plot(
        self,
        generator: NfoGenerator,
        season_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证季 NFO 包含剧情简介。"""
        output = os.path.join(temp_dir, "season.nfo")

        season_detail = {
            "season": MOCK_SEASON_DETAIL,
        }

        with patch.object(
            generator.tmdb,
            "get_detail",
            return_value={**MOCK_TV_DETAIL, **season_detail},
        ):
            generator.generate_season_nfo(season_result, 1, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "The first season" in content


# =============================================================
# 测试：单集 NFO 生成
# =============================================================


class TestGenerateEpisodeNfo:
    """单集 NFO 生成测试。"""

    def test_generate_episode_nfo(
        self,
        generator: NfoGenerator,
        episode_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证单集 NFO 文件被正常创建。"""
        output = os.path.join(temp_dir, "S01E01.nfo")

        episode_detail = {
            "episode": MOCK_EPISODE_DETAIL,
        }

        with patch.object(
            generator.tmdb,
            "get_detail",
            return_value={**MOCK_TV_DETAIL, **episode_detail},
        ):
            result = generator.generate_episode_nfo(episode_result, output)

        assert os.path.exists(output)
        assert result == os.path.abspath(output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<episodedetails>" in content
        assert "</episodedetails>" in content
        assert "<title>Pilot</title>" in content
        assert "<showtitle>Breaking Bad</showtitle>" in content

    def test_episode_nfo_has_season_and_episode_number(
        self,
        generator: NfoGenerator,
        episode_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证单集 NFO 包含季号和集号。"""
        output = os.path.join(temp_dir, "S01E01.nfo")

        episode_detail = {
            "episode": MOCK_EPISODE_DETAIL,
        }

        with patch.object(
            generator.tmdb,
            "get_detail",
            return_value={**MOCK_TV_DETAIL, **episode_detail},
        ):
            generator.generate_episode_nfo(episode_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<season>1</season>" in content
        assert "<episode>1</episode>" in content

    def test_episode_nfo_has_showtitle(
        self,
        generator: NfoGenerator,
        episode_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证单集 NFO 包含电视剧名。"""
        output = os.path.join(temp_dir, "S01E01.nfo")

        episode_detail = {
            "episode": MOCK_EPISODE_DETAIL,
        }

        with patch.object(
            generator.tmdb,
            "get_detail",
            return_value={**MOCK_TV_DETAIL, **episode_detail},
        ):
            generator.generate_episode_nfo(episode_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<showtitle>Breaking Bad</showtitle>" in content


# =============================================================
# 测试：图片 URL
# =============================================================


class TestGetImageUrls:
    """图片 URL 生成测试。"""

    def test_get_image_urls(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
    ) -> None:
        """验证返回正确的图片 URL 格式。"""
        with patch.object(
            generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
        ):
            urls = generator.get_image_urls(movie_result)

        assert "poster" in urls
        assert "backdrop" in urls
        assert "poster_small" in urls
        assert urls["poster"].startswith("https://image.tmdb.org/t/p/original")
        assert urls["poster_small"].startswith("https://image.tmdb.org/t/p/w500")
        assert "/kungfu_poster.jpg" in urls["poster"]
        assert "/kungfu_backdrop.jpg" in urls["backdrop"]

    def test_get_image_urls_no_poster(
        self,
        generator: NfoGenerator,
    ) -> None:
        """验证无海报时返回空字典。"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=99999,
            title="No Poster",
            original_title="No Poster",
        )

        with patch.object(
            generator.tmdb, "get_detail", return_value={}
        ):
            urls = generator.get_image_urls(result)

        assert urls == {}


# =============================================================
# 测试：图片下载
# =============================================================


class TestDownloadImages:
    """图片下载测试。"""

    def test_download_images(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证图片下载功能。"""
        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._image_downloader, "download",
                return_value="/fake/path/poster.jpg",
            ),
        ):
            result = generator.download_images(movie_result, temp_dir)

        # poster 应该包含在结果中
        assert "poster" in result

    def test_download_images_creates_fanart(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证背景图下载时同时创建 fanart.jpg。"""
        download_count = [0]

        def counting_download(url: str, output_path: str, **kwargs: Any) -> str:
            download_count[0] += 1
            return output_path

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._image_downloader, "download",
                side_effect=counting_download,
            ),
        ):
            generator.download_images(movie_result, temp_dir)

        # 应下载 3 次：poster、backdrop、fanart
        assert download_count[0] == 3

    def test_download_images_download_error(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证下载失败时优雅降级（不抛异常）。"""
        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._image_downloader, "download",
                side_effect=ImageDownloadError("Network error"),
            ),
        ):
            # 不应抛异常
            result = generator.download_images(movie_result, temp_dir)

        assert result == {}


# =============================================================
# 测试：最小 NFO 降级
# =============================================================


class TestMinimalNfoFallback:
    """最小 NFO 降级测试。"""

    def test_nfo_empty_detail_fallback(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证空 detail 时使用 MatchResult 中的基础信息。"""
        output = os.path.join(temp_dir, "fallback.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value={}
            ),
            patch.object(
                generator._movie_api, "credits",
                side_effect=Exception("API Error"),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        # 使用 MatchResult 中的基础字段
        assert "<title>功夫</title>" in content
        assert "<originaltitle>Kung Fu Hustle</originaltitle>" in content
        assert "<tmdbid>9470</tmdbid>" in content

    def test_nfo_no_detail_no_genre(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证无详情时，NFO 不包含 genre/director/actor 标签。"""
        output = os.path.join(temp_dir, "fallback.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value={}
            ),
            patch.object(
                generator._movie_api, "credits",
                side_effect=Exception("API Error"),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")
        assert "<genre>" not in content
        assert "<director>" not in content
        assert "<actor>" not in content


# =============================================================
# 测试：ImageDownloader
# =============================================================


class TestImageDownloader:
    """ImageDownloader 单元测试。"""

    def test_download_success(self, temp_dir: str) -> None:
        """验证成功下载图片。"""
        output = os.path.join(temp_dir, "test.jpg")

        with patch("httpx.Client") as mock_client:
            mock_response = MagicMock()
            mock_response.iter_bytes.return_value = [b"fake_image_data"]
            mock_client_instance = mock_client.return_value.__enter__.return_value
            mock_client_instance.get.return_value = mock_response
            mock_response.raise_for_status.return_value = None

            downloader = ImageDownloader()
            result = downloader.download(
                "https://example.com/image.jpg", output
            )

        assert os.path.exists(output)
        assert result == os.path.abspath(output)

    def test_download_skip_existing(self, temp_dir: str) -> None:
        """验证已存在的文件跳过下载。"""
        output = os.path.join(temp_dir, "test.jpg")
        Path(output).write_text("existing")

        downloader = ImageDownloader()
        with patch("httpx.Client") as mock_client:
            result = downloader.download(
                "https://example.com/image.jpg", output
            )

        # 不应调用 httpx
        mock_client.assert_not_called()
        assert result == os.path.abspath(output)

    def test_download_raises_on_empty_url(self, temp_dir: str) -> None:
        """验证空 URL 抛出异常。"""
        downloader = ImageDownloader()
        with pytest.raises(ImageDownloadError, match="URL 为空"):
            downloader.download("", os.path.join(temp_dir, "test.jpg"))

    def test_download_batch(self, temp_dir: str) -> None:
        """验证批量下载。"""
        with patch.object(ImageDownloader, "download") as mock_download:
            mock_download.return_value = "/fake/path/file.jpg"

            downloader = ImageDownloader()
            results = downloader.download_batch(
                {"poster.jpg": "https://example.com/poster.jpg"},
                temp_dir,
            )

            assert "poster.jpg" in results


# =============================================================
# 测试：XML 输出内容完整性
# =============================================================


class TestNfoContentCompleteness:
    """NFO 内容完整性测试。"""

    def test_movie_nfo_content_complete(
        self,
        generator: NfoGenerator,
        movie_result: MatchResult,
        temp_dir: str,
    ) -> None:
        """验证完整电影 NFO 包含所有必要字段。"""
        output = os.path.join(temp_dir, "movie.nfo")

        with (
            patch.object(
                generator.tmdb, "get_detail", return_value=MOCK_MOVIE_DETAIL
            ),
            patch.object(
                generator._movie_api, "credits",
                return_value=_make_credits_mock(
                    MOCK_MOVIE_CREDITS["cast"],
                    [{"name": d, "job": "Director"}
                     for d in MOCK_MOVIE_CREDITS["directors"]],
                ),
            ),
        ):
            generator.generate_movie_nfo(movie_result, output)

        content = Path(output).read_text(encoding="utf-8")

        # 必要根元素
        assert "<movie>" in content

        # 核心元数据
        assert "<title>" in content
        assert "<originaltitle>" in content
        assert "<sorttitle>" in content
        assert "<tmdbid>" in content
        assert "<uniqueid" in content

        # 时间信息
        assert "<year>" in content
        assert "<premiered>" in content

        # 内容描述
        assert "<plot>" in content
        assert "<outline>" in content

        # 分类
        assert "<genre>" in content

        # 评分和片长
        assert "<rating>" in content
        assert "<runtime>" in content

        # 演员和导演
        assert "<director>" in content
        assert "<actor>" in content
