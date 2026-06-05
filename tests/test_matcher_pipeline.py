"""
匹配管道编排引擎单元测试

使用 unittest.mock 模拟所有子模块，隔离测试管道的 5 级优先级链逻辑。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from medialens.matcher.matcher_pipeline import MatcherPipeline
from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaCandidate,
    MediaLensConfig,
    MediaType,
    ParsedMedia,
)


# =============================================================
# 辅助：构建 Mock 对象
# =============================================================


def _make_mock_obj(**attrs: Any) -> MagicMock:
    """创建一个 MagicMock 对象并设置属性。"""
    obj = MagicMock()
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


# =============================================================
# 共享测试数据
# =============================================================

SAMPLE_CANDIDATES = [
    MediaCandidate(
        tmdb_id=603,
        title="The Matrix",
        original_title="The Matrix",
        year=1999,
        media_type=MediaType.MOVIE,
        overview="A computer hacker learns about the true nature of reality.",
        match_score=98.0,
    ),
    MediaCandidate(
        tmdb_id=604,
        title="The Matrix Reloaded",
        original_title="The Matrix Reloaded",
        year=2003,
        media_type=MediaType.MOVIE,
        match_score=55.0,
    ),
]

SAMPLE_MEDIUM_CANDIDATES = [
    MediaCandidate(
        tmdb_id=603,
        title="The Matrix",
        original_title="The Matrix",
        year=1999,
        media_type=MediaType.MOVIE,
        match_score=70.0,
    ),
]

SAMPLE_LOW_CANDIDATES = [
    MediaCandidate(
        tmdb_id=999,
        title="Some Unknown Movie",
        original_title="Some Unknown Movie",
        year=2020,
        media_type=MediaType.MOVIE,
        match_score=35.0,
    ),
]


def make_tmdb_exact_result(candidates: Optional[list[MediaCandidate]] = None) -> MatchResult:
    """构建 TMDB 精确匹配结果。"""
    return MatchResult(
        matched=True,
        media_type=MediaType.MOVIE,
        tmdb_id=603,
        title="The Matrix",
        original_title="The Matrix",
        year=1999,
        source=MatchSource.TMDB_EXACT,
        confidence=98.0,
        candidates=candidates or [],
    )


def make_tmdb_fuzzy_result(
    confidence: float = 70.0,
    candidates: Optional[list[MediaCandidate]] = None,
) -> MatchResult:
    """构建 TMDB 模糊匹配结果。"""
    best = (candidates or SAMPLE_MEDIUM_CANDIDATES)[0]
    return MatchResult(
        matched=False,
        media_type=MediaType.MOVIE,
        tmdb_id=best.tmdb_id,
        title=best.title,
        original_title=best.original_title,
        year=best.year,
        source=MatchSource.TMDB_FUZZY,
        confidence=confidence,
        candidates=candidates or [],
    )


# =============================================================
# Fixtures
# =============================================================


@pytest.fixture
def base_config() -> MediaLensConfig:
    """基础配置（未配 LLM key）。"""
    return MediaLensConfig(
        tmdb_api_key="test_key",
        tmdb_language="zh-CN",
        tmdb_confidence_threshold=80,
        llm_confidence_threshold=60,
        llm_api_key="",
        db_path=":memory:",
    )


@pytest.fixture
def llm_config() -> MediaLensConfig:
    """含 LLM 配置。"""
    return MediaLensConfig(
        tmdb_api_key="test_key",
        tmdb_language="zh-CN",
        tmdb_confidence_threshold=80,
        llm_confidence_threshold=60,
        llm_api_key="sk-test",
        llm_model="gpt-4o-mini",
        db_path=":memory:",
    )


# =============================================================
# 测试类：缓存命中
# =============================================================


class TestCacheHit:
    """Level 1: 缓存命中场景测试"""

    def test_cache_hit_returns_early(self, base_config: MediaLensConfig) -> None:
        """验证缓存命中直接返回 CACHE_HIT 结果。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # Mock DB 返回缓存记录
        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = {
            "tmdb_id": 603,
            "media_type": "movie",
            "matched_title": "The Matrix",
            "matched_year": 1999,
            "overview": "A computer hacker...",
            "poster_path": "/matrix.jpg",
            "backdrop_path": "/matrix_bg.jpg",
        }
        pipeline.db = mock_db

        # Mock hash 计算（避免文件系统访问）
        with patch.object(pipeline, "compute_file_hash", return_value="abcdef123456"):
            result = pipeline.match("/media/The Matrix (1999).mkv")

        assert result.matched is True
        assert result.source == MatchSource.CACHE_HIT
        assert result.confidence == 100.0
        assert result.tmdb_id == 603
        assert result.title == "The Matrix"

    def test_cache_miss_continues(self, base_config: MediaLensConfig) -> None:
        """验证缓存未命中时继续执行 TMDB 匹配。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # Mock DB 返回 None（缓存未命中）
        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        # Mock TMDB 精确匹配
        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_none"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_exact_result(),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/The Matrix (1999).mkv")

        assert result.matched is True
        assert result.source == MatchSource.TMDB_EXACT


# =============================================================
# 测试类：TMDB 精确匹配
# =============================================================


class TestTmdbExactMatch:
    """Level 2: TMDB 精确匹配测试"""

    def test_tmdb_exact_match_high_confidence(
        self, base_config: MediaLensConfig
    ) -> None:
        """验证 TMDB 返回高置信度时直接返回 TMDB_EXACT。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # Mock DB 返回 None（缓存未命中）
        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_exact"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_exact_result(candidates=SAMPLE_CANDIDATES),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/The Matrix (1999).mkv")

        assert result.matched is True
        assert result.source == MatchSource.TMDB_EXACT
        assert result.confidence == 98.0
        assert result.tmdb_id == 603

    def test_tmdb_exact_result_has_candidates(
        self, base_config: MediaLensConfig
    ) -> None:
        """验证精确匹配结果包含候选列表。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_exact2"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_exact_result(candidates=SAMPLE_CANDIDATES),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/The Matrix (1999).mkv")

        assert len(result.candidates) == 2


# =============================================================
# 测试类：TMDB 模糊匹配
# =============================================================


class TestTmdbFuzzyMatch:
    """Level 3: TMDB 模糊匹配测试"""

    def test_tmdb_fuzzy_match(self, base_config: MediaLensConfig) -> None:
        """验证中等置信度（>= 60）返回 TMDB_FUZZY，不进入 LLM。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_fuzzy"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=70.0, candidates=SAMPLE_MEDIUM_CANDIDATES
                ),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/Some Movie.mkv")

        assert result.matched is True
        assert result.source == MatchSource.TMDB_FUZZY
        assert result.confidence == 70.0

    def test_tmdb_fuzzy_does_not_call_llm(
        self, base_config: MediaLensConfig
    ) -> None:
        """验证 TMDB 模糊匹配不需要 LLM 裁决。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_fuzzy2"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=65.0, candidates=SAMPLE_MEDIUM_CANDIDATES
                ),
            ),
            patch.object(pipeline, "_run_llm_match") as mock_llm,
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/Some Movie.mkv")

        assert result.matched is True
        assert result.source == MatchSource.TMDB_FUZZY
        mock_llm.assert_not_called()


# =============================================================
# 测试类：LLM 补充匹配
# =============================================================


class TestLlmFallback:
    """Level 4: LLM 补充裁决测试"""

    def test_llm_fallback(self, llm_config: MediaLensConfig) -> None:
        """验证低置信度 + LLM 可用时，LLM 补充匹配。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=llm_config)
        # 此时 pipeline.llm_matcher 已被初始化

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        # 构造一个 LLM 可以选中的候选
        llm_selected = MediaCandidate(
            tmdb_id=603,
            title="The Matrix",
            original_title="The Matrix",
            year=1999,
            media_type=MediaType.MOVIE,
            match_score=85.0,
        )

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_llm"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=35.0, candidates=SAMPLE_LOW_CANDIDATES
                ),
            ),
            patch.object(
                pipeline.llm_matcher, "verify", return_value=llm_selected,
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/Fuzzy Movie.mkv")

        assert result.matched is True
        assert result.source == MatchSource.LLM_VERIFIED
        assert result.tmdb_id == 603
        assert result.confidence == 85.0

    def test_llm_fallback_no_candidates(self, llm_config: MediaLensConfig) -> None:
        """验证 TMDB 无候选时 LLM 不被调用。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=llm_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_empty"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=20.0, candidates=[]
                ),
            ),
            patch.object(pipeline.llm_matcher, "verify") as mock_verify,
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/Empty.mkv")

        assert result.matched is False
        mock_verify.assert_not_called()

    def test_llm_not_available_skips(self, base_config: MediaLensConfig) -> None:
        """验证 LLM 未配置时跳过 LLM 步骤。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # 确认 LLM 匹配器为 None
        assert pipeline.llm_matcher is None

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_nollm"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=30.0, candidates=SAMPLE_LOW_CANDIDATES
                ),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/No LLM.mkv")

        # 无 LLM 时，低置信度返回未匹配
        assert result.matched is False
        assert result.confidence == 30.0

    def test_llm_returns_none(self, llm_config: MediaLensConfig) -> None:
        """验证 LLM 无法裁决时返回未匹配。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=llm_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_llm_none"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=35.0, candidates=SAMPLE_LOW_CANDIDATES
                ),
            ),
            patch.object(pipeline.llm_matcher, "verify", return_value=None),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/LLM None.mkv")

        assert result.matched is False
        # 保留 TMDB 的置信度
        assert result.confidence == 35.0


# =============================================================
# 测试类：完全未匹配
# =============================================================


class TestNoMatch:
    """Level 5: 所有路径都失败"""

    def test_no_match_found(self, base_config: MediaLensConfig) -> None:
        """验证所有匹配路径都失败时返回未匹配结果。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_none"),
            patch.object(
                pipeline, "_run_tmdb_match", return_value=None,
            ),
        ):
            result = pipeline.match("/media/Unknown.mkv")

        assert result.matched is False
        assert result.confidence == 0.0
        assert result.candidates == []

    def test_unknown_format(self, base_config: MediaLensConfig) -> None:
        """验证无法识别的格式直接返回未匹配。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_unknown"),
            patch.object(
                pipeline, "_run_tmdb_match", return_value=None,
            ),
        ):
            result = pipeline.match("/media/file.txt")

        assert result.matched is False

    def test_tmdb_match_raises_exception(self, base_config: MediaLensConfig) -> None:
        """验证 TMDB 异常时降级返回未匹配。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # 同时 mock 缓存查询（避免真实 DB 初始化）和 detect_format 异常
        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_err"),
            patch.object(pipeline.db, "get_match_by_hash", return_value=None),
            patch("medialens.matcher.matcher_pipeline.detect_format",
                  side_effect=PermissionError("Access denied")),
        ):
            result = pipeline.match("/media/Error.mkv")

        assert result.matched is False

    def test_llm_raises_exception(self, llm_config: MediaLensConfig) -> None:
        """验证 LLM 异常时降级不崩溃。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=llm_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_llm_err"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_fuzzy_result(
                    confidence=30.0, candidates=SAMPLE_LOW_CANDIDATES
                ),
            ),
            patch.object(
                pipeline.llm_matcher, "verify",
                side_effect=RuntimeError("LLM API error"),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            result = pipeline.match("/media/LLM Error.mkv")

        assert result.matched is False
        assert result.confidence == 30.0


# =============================================================
# 测试类：批量匹配
# =============================================================


class TestBatchMatch:
    """批量匹配测试"""

    def test_batch_match_all_succeed(self, base_config: MediaLensConfig) -> None:
        """验证批量匹配全部成功。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        with (
            patch.object(pipeline, "compute_file_hash", return_value="batch_hash"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_exact_result(candidates=SAMPLE_CANDIDATES),
            ),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            paths = [
                "/media/Movie1.mkv",
                "/media/Movie2.mkv",
                "/media/Movie3.mkv",
            ]
            results = pipeline.match_batch(paths)

        assert len(results) == 3
        assert all(r.matched for r in results)
        assert all(r.source == MatchSource.TMDB_EXACT for r in results)

    def test_batch_match_partial_failure(self, base_config: MediaLensConfig) -> None:
        """验证批量匹配中部分失败不影响其他。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.get_match_by_hash.return_value = None
        pipeline.db = mock_db

        call_count = 0

        def _mock_tmdb_match(path: str) -> Optional[MatchResult]:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("TMDB failure")
            return make_tmdb_exact_result(candidates=SAMPLE_CANDIDATES)

        with (
            patch.object(pipeline, "compute_file_hash", return_value="batch_hash2"),
            patch.object(pipeline, "_run_tmdb_match", side_effect=_mock_tmdb_match),
            patch.object(pipeline.db, "save_match", return_value=1),
        ):
            paths = [
                "/media/Good1.mkv",
                "/media/Good2.mkv",  # 会失败
                "/media/Good3.mkv",
            ]
            results = pipeline.match_batch(paths)

        assert len(results) == 3
        assert results[0].matched is True
        assert results[1].matched is False   # 第二个失败
        assert results[2].matched is True

    def test_batch_match_caches_exceptions(self, base_config: MediaLensConfig) -> None:
        """验证批量匹配中异常被捕获。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        with (
            patch.object(pipeline, "match", side_effect=Exception("Unexpected")),
        ):
            results = pipeline.match_batch(["/media/A.mkv"])

        assert len(results) == 1
        assert results[0].matched is False
        assert results[0].confidence == 0.0


# =============================================================
# 测试类：哈希计算
# =============================================================


class TestComputeFileHash:
    """文件哈希计算测试"""

    def test_compute_file_hash(self, tmp_path: Path, base_config: MediaLensConfig) -> None:
        """验证文件的 SHA256 哈希计算（前 64KB）。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # 创建测试文件（写入 100KB 数据，验证只读前 64KB）
        test_file = tmp_path / "test_movie.mkv"
        content = b"A" * 100 * 1024  # 100KB
        test_file.write_bytes(content)

        file_hash = pipeline.compute_file_hash(str(test_file))

        # 验证只取前 64KB 计算
        expected_hash = hashlib.sha256(b"A" * 65536).hexdigest()
        assert file_hash == expected_hash

    def test_compute_file_hash_small_file(
        self, tmp_path: Path, base_config: MediaLensConfig
    ) -> None:
        """验证小文件哈希（小于 64KB 全量计算）。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        test_file = tmp_path / "small.mkv"
        content = b"Small file content"
        test_file.write_bytes(content)

        file_hash = pipeline.compute_file_hash(str(test_file))

        expected_hash = hashlib.sha256(content).hexdigest()
        assert file_hash == expected_hash

    def test_compute_dir_hash(self, tmp_path: Path, base_config: MediaLensConfig) -> None:
        """验证目录哈希计算（路径 + 文件名列表）。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # 模拟 BDMV 目录结构
        bdmv_dir = tmp_path / "BDMV" / "STREAM"
        bdmv_dir.mkdir(parents=True)

        (bdmv_dir / "00001.m2ts").write_bytes(b"data1")
        (bdmv_dir / "00002.m2ts").write_bytes(b"data2")

        # 计算 BDMV 父目录的哈希
        hash_result = pipeline.compute_file_hash(str(tmp_path / "BDMV"))

        # 验证是目录哈希（基于路径字符串 + 文件名）
        hasher = hashlib.sha256()
        hasher.update(str((tmp_path / "BDMV").resolve()).encode("utf-8"))
        # 目录中的文件名按字母序
        expected_hash = hasher.hexdigest()
        # 注意：BDMV 目录下没有文件（文件在子目录 STREAM 中，iterdir 不递归）
        assert hash_result == expected_hash

    def test_compute_nonexistent_path(self, base_config: MediaLensConfig) -> None:
        """验证不存在的路径使用路径字符串计算哈希。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        file_hash = pipeline.compute_file_hash("/nonexistent/path.mkv")

        expected_hash = hashlib.sha256("/nonexistent/path.mkv".encode("utf-8")).hexdigest()
        assert file_hash == expected_hash

    def test_compute_dir_hash_with_file_names(
        self, tmp_path: Path, base_config: MediaLensConfig
    ) -> None:
        """验证目录哈希包含目录中文件名。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        # 创建一个"目录"作为被计算的路径
        movie_dir = tmp_path / "My Movie (2020)"
        movie_dir.mkdir()
        (movie_dir / "My Movie (2020).mkv").write_bytes(b"data")
        (movie_dir / "subs.srt").write_bytes(b"sub data")

        hash_result = pipeline.compute_file_hash(str(movie_dir))

        # 手动计算预期值
        hasher = hashlib.sha256()
        hasher.update(str(movie_dir.resolve()).encode("utf-8"))
        hasher.update(b"My Movie (2020).mkv")
        hasher.update(b"subs.srt")
        expected = hasher.hexdigest()

        assert hash_result == expected


# =============================================================
# 测试类：用户纠正回调
# =============================================================


class TestUpdateMatchConfidence:
    """用户纠正回调测试"""

    def test_update_match_confidence(self, base_config: MediaLensConfig) -> None:
        """验证用户纠正更新数据库。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.update_match.return_value = True
        pipeline.db = mock_db

        result = pipeline.update_match_confidence(
            match_id=42,
            user_corrected_title="The Matrix",
        )

        assert result is True
        mock_db.update_match.assert_called_once_with(
            42,
            {
                "matched_title": "The Matrix",
                "match_source": MatchSource.USER_CONFIRMED.value,
                "confidence": 100.0,
            },
        )

    def test_update_match_confidence_not_found(self, base_config: MediaLensConfig) -> None:
        """验证更新不存在的记录返回 False。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        mock_db = MagicMock()
        mock_db.update_match.return_value = False
        pipeline.db = mock_db

        result = pipeline.update_match_confidence(
            match_id=999,
            user_corrected_title="Nonexistent",
        )

        assert result is False


# =============================================================
# 测试类：子模块初始化
# =============================================================


class TestInit:
    """初始化测试"""

    def test_init_without_llm(self, base_config: MediaLensConfig) -> None:
        """验证无 LLM 配置时不初始化 LLM 匹配器。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)
        assert pipeline.llm_matcher is None

    def test_init_with_llm(self, llm_config: MediaLensConfig) -> None:
        """验证有 LLM 配置时初始化 LLM 匹配器。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=llm_config)
        assert pipeline.llm_matcher is not None

    def test_init_tmdb_matcher(self, base_config: MediaLensConfig) -> None:
        """验证 TMDB 匹配器始终初始化。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)
        assert pipeline.tmdb_matcher is not None
        assert pipeline.tmdb_matcher.api_key == "test_key"


# =============================================================
# 测试类：完整流程（集成行为验证）
# =============================================================


class TestPipelineFlow:
    """完整管道流程行为验证"""

    def test_full_pipeline_success(
        self, base_config: MediaLensConfig
    ) -> None:
        """验证完整管道走完 Cache -> TMDB 路径。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_exact_result(candidates=SAMPLE_CANDIDATES),
            ),
            patch.object(pipeline.db, "get_match_by_hash", return_value=None),
            patch.object(pipeline.db, "save_match", return_value=1) as mock_save,
        ):
            result = pipeline.match("/media/The Matrix (1999).mkv")

        assert result.matched is True
        assert result.source == MatchSource.TMDB_EXACT
        mock_save.assert_called_once()

    def test_pipeline_saves_to_db_on_success(
        self, base_config: MediaLensConfig
    ) -> None:
        """验证匹配成功后保存到数据库。"""
        pipeline = MatcherPipeline(tmdb_api_key="test_key", config=base_config)

        with (
            patch.object(pipeline, "compute_file_hash", return_value="hash_save"),
            patch.object(
                pipeline, "_run_tmdb_match",
                return_value=make_tmdb_exact_result(candidates=SAMPLE_CANDIDATES),
            ),
            patch.object(pipeline.db, "get_match_by_hash", return_value=None),
            patch.object(pipeline.db, "save_match", return_value=1) as mock_save,
        ):
            pipeline.match("/media/Save Test.mkv")

        # 验证 save_match 被调用，且传入了正确的 file_hash
        mock_save.assert_called_once()
        args, kwargs = mock_save.call_args
        assert kwargs.get("file_hash") == "hash_save"
