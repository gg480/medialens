"""
LLM 补充裁决模块单元测试

覆盖 build_prompt、parse_response、is_available、get_llm_confidence 和 verify。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from medialens.matcher.llm_matcher import LlmMatcher
from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaCandidate,
    MediaType,
    ParsedMedia,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def matcher() -> LlmMatcher:
    """创建一个带 API key 的 LlmMatcher 实例。"""
    return LlmMatcher(
        provider="openai",
        api_key="sk-test-key",
        model="gpt-4o-mini",
    )


@pytest.fixture
def matcher_no_key() -> LlmMatcher:
    """创建一个无 API key 的 LlmMatcher 实例。"""
    return LlmMatcher(
        provider="openai",
        api_key="",
        model="gpt-4o-mini",
    )


@pytest.fixture
def matcher_ollama() -> LlmMatcher:
    """创建一个 Ollama 的 LlmMatcher 实例。"""
    return LlmMatcher(
        provider="ollama",
        api_key="",
        model="ollama/qwen2.5",
    )


@pytest.fixture
def movie_parsed() -> ParsedMedia:
    """电影解析结果 fixture。"""
    return ParsedMedia(
        raw_path=Path("/media/movies/The Matrix (1999).mkv"),
        raw_filename="The Matrix (1999).mkv",
        file_format=FileFormat.SINGLE_FILE,
        title="The Matrix",
        year=1999,
    )


@pytest.fixture
def tv_parsed() -> ParsedMedia:
    """剧集解析结果 fixture。"""
    return ParsedMedia(
        raw_path=Path("/media/tv/Breaking Bad S01E01.mkv"),
        raw_filename="Breaking Bad S01E01.mkv",
        file_format=FileFormat.TV_EPISODE,
        title="Breaking Bad",
        year=2008,
        season=1,
        episode=1,
        is_episode=True,
    )


@pytest.fixture
def candidates() -> list[MediaCandidate]:
    """TMDB 候选列表 fixture。"""
    return [
        MediaCandidate(
            tmdb_id=603,
            title="黑客帝国",
            original_title="The Matrix",
            year=1999,
            media_type=MediaType.MOVIE,
            overview="一名黑客发现现实世界只是由机器创造的虚拟世界。",
            match_score=85.0,
        ),
        MediaCandidate(
            tmdb_id=604,
            title="黑客帝国2：重装上阵",
            original_title="The Matrix Reloaded",
            year=2003,
            media_type=MediaType.MOVIE,
            overview="尼奥继续与机器战斗，寻找救世主的真相。",
            match_score=45.0,
        ),
        MediaCandidate(
            tmdb_id=605,
            title="黑客帝国3：矩阵革命",
            original_title="The Matrix Revolutions",
            year=2003,
            media_type=MediaType.MOVIE,
            overview="人类与机器的最终决战。",
            match_score=30.0,
        ),
    ]


# =============================================================
# build_prompt 测试
# =============================================================


class TestBuildPrompt:
    """测试 build_prompt 方法的结构和内容。"""

    def test_build_prompt_contains_parsed_info(
        self,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """验证 prompt 包含文件名、解析标题、年份等解析信息。"""
        prompt = matcher.build_prompt(movie_parsed, candidates)
        assert "The Matrix (1999).mkv" in prompt
        assert "The Matrix" in prompt
        assert "1999" in prompt
        assert "single_file" in prompt

    def test_build_prompt_contains_candidates(
        self,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """验证 prompt 包含所有候选信息（标题、年份、简介片段）。"""
        prompt = matcher.build_prompt(movie_parsed, candidates)
        assert "黑客帝国" in prompt
        assert "1999" in prompt
        # 所有候选的标题都在 prompt 中
        assert "黑客帝国2：重装上阵" in prompt
        assert "黑客帝国3：矩阵革命" in prompt
        # 序号
        assert "1." in prompt
        assert "2." in prompt
        assert "3." in prompt
        # JSON 格式要求
        assert "selected" in prompt

    def test_build_prompt_tv_episode(
        self,
        matcher: LlmMatcher,
        tv_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """验证剧集文件时 prompt 包含季、集信息。"""
        prompt = matcher.build_prompt(tv_parsed, candidates)
        assert "Breaking Bad S01E01.mkv" in prompt
        assert "Breaking Bad" in prompt
        assert "季: 1" in prompt
        assert "集: 1" in prompt
        assert "tv_episode" in prompt

    def test_build_prompt_contains_json_instruction(
        self,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """验证 prompt 包含 JSON 格式要求的指令。"""
        prompt = matcher.build_prompt(movie_parsed, candidates)
        assert "JSON" in prompt or "json" in prompt
        assert "selected" in prompt
        assert "confidence" in prompt
        assert "reason" in prompt


# =============================================================
# parse_response 测试
# =============================================================


class TestParseResponse:
    """测试 parse_response 方法对 LLM 返回值的解析。"""

    def test_parse_response_valid_json(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """有效 JSON 且 selected 在范围内，返回对应候选。"""
        response = '{"selected": 1, "confidence": 95, "reason": "标题和年份完全匹配"}'
        result = matcher.parse_response(response, candidates)
        assert result is not None
        assert result.tmdb_id == 603
        assert result.title == "黑客帝国"
        assert result.match_score == 95.0

    def test_parse_response_none_selected(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """selected == -1 表示 LLM 无法确定，返回 None。"""
        response = '{"selected": -1, "confidence": 30, "reason": "无法确定匹配项"}'
        result = matcher.parse_response(response, candidates)
        assert result is None

    def test_parse_response_invalid_json(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """无效 JSON 返回 None。"""
        response = "这不是一个 JSON 字符串"
        result = matcher.parse_response(response, candidates)
        assert result is None

    def test_parse_response_out_of_range(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """selected 序号超出候选列表范围，返回 None。"""
        response = '{"selected": 99, "confidence": 80, "reason": "选了个不存在的"}'
        result = matcher.parse_response(response, candidates)
        assert result is None

    def test_parse_response_zero_index(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """序号从 0 开始（无效，prompt 中从 1 开始），返回 None。"""
        response = '{"selected": 0, "confidence": 90, "reason": "错误序号"}'
        result = matcher.parse_response(response, candidates)
        assert result is None

    def test_parse_response_with_code_fence(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """LLM 响应包裹在 ```json ``` 代码块中。"""
        response = """```json
{"selected": 2, "confidence": 70, "reason": "年份匹配"}
```"""
        result = matcher.parse_response(response, candidates)
        assert result is not None
        assert result.tmdb_id == 604
        assert result.title == "黑客帝国2：重装上阵"

    def test_parse_response_with_code_block(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """LLM 响应包裹在 ``` 普通代码块中。"""
        response = """```{"selected": 3, "confidence": 60, "reason": "第三候选"}
```"""
        result = matcher.parse_response(response, candidates)
        assert result is not None
        assert result.tmdb_id == 605

    def test_parse_response_sets_match_score(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """解析后的候选的 match_score 被设置为 LLM 返回的 confidence。"""
        response = '{"selected": 1, "confidence": 88, "reason": "匹配"}'
        result = matcher.parse_response(response, candidates)
        assert result is not None
        assert result.match_score == 88.0

    def test_parse_response_missing_confidence(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """LLM 未返回 confidence 时，match_score 保持默认值 0.0。"""
        response = '{"selected": 1, "reason": "匹配"}'
        result = matcher.parse_response(response, candidates)
        assert result is not None
        assert result.match_score == 0.0

    def test_parse_response_empty_string(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """空字符串响应返回 None。"""
        result = matcher.parse_response("", candidates)
        assert result is None

    def test_parse_response_selected_as_string_number(
        self,
        matcher: LlmMatcher,
        candidates: list[MediaCandidate],
    ) -> None:
        """selected 是数字字符串（如 "1"）也能正确解析。"""
        response = '{"selected": "1", "confidence": 90, "reason": "匹配"}'
        result = matcher.parse_response(response, candidates)
        assert result is not None
        assert result.tmdb_id == 603


# =============================================================
# is_available 测试
# =============================================================


class TestIsAvailable:
    """测试 is_available 方法。"""

    def test_is_available_no_key(self, matcher_no_key: LlmMatcher) -> None:
        """没有配置 API key 时返回 False。"""
        assert matcher_no_key.is_available() is False

    def test_is_available_with_key(self, matcher: LlmMatcher) -> None:
        """配置了 API key 时返回 True。"""
        assert matcher.is_available() is True

    def test_is_available_ollama_no_key(
        self,
        matcher_ollama: LlmMatcher,
    ) -> None:
        """Ollama 不需要 API key，有模型名即可用。"""
        assert matcher_ollama.is_available() is True

    def test_is_available_ollama_empty_model(self) -> None:
        """Ollama 模型名为空时不可用。"""
        m = LlmMatcher(provider="ollama", api_key="", model="")
        assert m.is_available() is False


# =============================================================
# get_llm_confidence 测试
# =============================================================


class TestGetLlmConfidence:
    """测试 get_llm_confidence 静态方法。"""

    def test_llm_source_returns_confidence(self) -> None:
        """来源为 LLM_VERIFIED 时返回置信度。"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=603,
            title="The Matrix",
            original_title="The Matrix",
            year=1999,
            source=MatchSource.LLM_VERIFIED,
            confidence=85.0,
        )
        assert LlmMatcher.get_llm_confidence(result) == 85.0

    def test_tmdb_source_returns_zero(self) -> None:
        """非 LLM 来源返回 0.0。"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=603,
            title="The Matrix",
            original_title="The Matrix",
            year=1999,
            source=MatchSource.TMDB_FUZZY,
            confidence=90.0,
        )
        assert LlmMatcher.get_llm_confidence(result) == 0.0

    def test_cache_source_returns_zero(self) -> None:
        """缓存来源返回 0.0。"""
        result = MatchResult(
            matched=True,
            media_type=MediaType.MOVIE,
            tmdb_id=603,
            title="The Matrix",
            original_title="The Matrix",
            year=1999,
            source=MatchSource.CACHE_HIT,
            confidence=100.0,
        )
        assert LlmMatcher.get_llm_confidence(result) == 0.0


# =============================================================
# verify 集成测试（mock litellm）
# =============================================================


class TestVerify:
    """测试 verify 方法的完整流程（mock LLM API）。"""

    def test_verify_no_candidates(
        self,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
    ) -> None:
        """候选列表为空时返回 None。"""
        result = matcher.verify(movie_parsed, [])
        assert result is None

    def test_verify_not_available(
        self,
        matcher_no_key: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """LLM 未配置时返回 None。"""
        result = matcher_no_key.verify(movie_parsed, candidates)
        assert result is None

    @patch("litellm.completion")
    def test_verify_calls_llm_and_parses(
        self,
        mock_completion,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """调用 LLM 并正确解析返回的候选。"""
        # mock litellm 返回值
        mock_response = type("Response", (), {
            "choices": [
                type("Choice", (), {
                    "message": type("Message", (), {
                        "content": '{"selected": 1, "confidence": 95, "reason": "匹配"}'
                    })()
                })()
            ]
        })()
        mock_completion.return_value = mock_response

        result = matcher.verify(movie_parsed, candidates)

        assert result is not None
        assert result.tmdb_id == 603
        assert result.title == "黑客帝国"
        assert result.match_score == 95.0

        # 验证 litellm.completion 被正确调用
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args[1]
        assert call_kwargs["model"] == "gpt-4o-mini"
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["timeout"] == 30

    @patch("litellm.completion")
    def test_verify_llm_error_returns_none(
        self,
        mock_completion,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """LLM 调用异常时返回 None。"""
        mock_completion.side_effect = RuntimeError("API timeout")

        result = matcher.verify(movie_parsed, candidates)
        assert result is None

    @patch("litellm.completion")
    def test_verify_llm_returns_none(
        self,
        mock_completion,
        matcher: LlmMatcher,
        movie_parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> None:
        """LLM 返回无法确定的响应时返回 None。"""
        mock_response = type("Response", (), {
            "choices": [
                type("Choice", (), {
                    "message": type("Message", (), {
                        "content": '{"selected": -1, "confidence": 20, "reason": "无法确定"}'
                    })()
                })()
            ]
        })()
        mock_completion.return_value = mock_response

        result = matcher.verify(movie_parsed, candidates)
        assert result is None
