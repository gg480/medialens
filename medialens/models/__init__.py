"""
MediaLens 核心数据模型

定义整个系统中流通的数据类型，包括：
- FileFormat: 文件格式枚举（单文件/蓝光原盘/ISO/剧集）
- ParsedMedia: 解析器输出的结构化媒体信息
- MatchResult: 匹配结果（含置信度和来源）
- MediaConfig: 用户配置
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class FileFormat(enum.Enum):
    """文件格式类型，决定使用哪种解析策略"""

    SINGLE_FILE = "single_file"        # 单文件 .mkv/.mp4/.avi
    BLURAY_BDMV = "bluray_bdmv"        # 蓝光原盘 BDMV 目录结构
    BDISO = "bdiso"                     # ISO/BDISO 镜像文件
    TV_EPISODE = "tv_episode"           # 电视剧单集
    TV_SEASON = "tv_season"             # 电视剧季目录
    UNKNOWN = "unknown"                 # 无法识别


class MatchSource(enum.Enum):
    """匹配结果来源，决定后续处理策略"""

    TMDB_EXACT = "tmdb_exact"           # TMDB 精确匹配（标题+年份）
    TMDB_FUZZY = "tmdb_fuzzy"           # TMDB 模糊匹配（达阈值）
    LLM_VERIFIED = "llm_verified"       # LLM 补充验证
    CACHE_HIT = "cache_hit"             # RAG 缓存命中
    USER_CONFIRMED = "user_confirmed"   # 用户手动确认


class MediaType(enum.Enum):
    """媒体类型"""
    MOVIE = "movie"
    TV = "tv"


@dataclass
class ParsedMedia:
    """
    解析器输出的结构化媒体信息
    
    从文件名/目录名中提取的结构化数据，
    后续传递给 Matcher 做匹配。
    """
    # 原始信息
    raw_path: Path                          # 原始文件路径
    raw_filename: str                       # 原始文件名（无路径）
    file_format: FileFormat                 # 文件格式类型
    
    # 解析结果
    title: Optional[str] = None             # 提取的标题
    original_title: Optional[str] = None    # 原始标题（guessit 输出）
    year: Optional[int] = None              # 提取的年份
    season: Optional[int] = None            # 季号（电视剧）
    episode: Optional[int] = None           # 集号（电视剧）
    episode_count: Optional[int] = None     # 总集数
    
    # 质量信息
    source: Optional[str] = None            # BluRay / WEB-DL / HDTV
    resolution: Optional[str] = None        # 1080p / 2160p / 720p
    video_codec: Optional[str] = None       # h264 / h265 / xvid
    audio_codec: Optional[str] = None       # dts / ac3 / aac
    release_group: Optional[str] = None     # 压制组
    
    # BDMV 特有
    bdmv_parent: Optional[str] = None       # BDMV 父目录名
    m2ts_count: int = 0                     # STREAM/ 下 m2ts 文件数
    largest_m2ts: Optional[str] = None      # 最大的 m2ts 文件（主电影）
    
    # 元信息
    confidence: float = 0.0                 # 解析置信度 0-1
    is_episode: bool = False                # 是否是剧集
    
    @property
    def display_name(self) -> str:
        """用于显示的名称"""
        if self.file_format == FileFormat.BLURAY_BDMV and self.bdmv_parent:
            return self.bdmv_parent
        return self.title or self.raw_filename


@dataclass
class MediaCandidate:
    """TMDB 搜索候选"""
    tmdb_id: int
    title: str
    original_title: str
    year: Optional[int]
    media_type: MediaType
    overview: str = ""
    poster_path: Optional[str] = None
    match_score: float = 0.0               # 匹配分数 0-100


@dataclass
class MatchResult:
    """
    匹配结果
    
    经过匹配管道（TMDB → LLM → 人工）后输出最终匹配结果。
    """
    # 匹配结果
    matched: bool                           # 是否成功匹配
    media_type: MediaType                   # 媒体类型
    tmdb_id: int                            # TMDB ID
    title: str                              # 匹配到的标题
    original_title: str                     # 原始标题
    year: Optional[int] = None              # 年份
    
    # 匹配过程
    source: MatchSource = MatchSource.TMDB_FUZZY  # 匹配来源
    confidence: float = 0.0                 # 最终置信度 0-100
    candidates: list[MediaCandidate] = field(default_factory=list)  # 候选列表
    raw_response: Optional[str] = None      # LLM 原始响应
    
    # TMDB 详细信息（刮削用）
    overview: str = ""
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    
    # 电视剧特有
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_count: Optional[int] = None
    
    @property
    def display_name(self) -> str:
        parts = [self.title]
        if self.year:
            parts.append(f"({self.year})")
        if self.season is not None:
            parts.append(f"S{self.season:02d}")
        if self.episode is not None:
            parts.append(f"E{self.episode:02d}")
        return " ".join(parts)


@dataclass
class FileOperation:
    """文件操作记录"""
    source_path: Path                       # 源文件路径
    target_path: Path                       # 目标路径
    operation: str                          # hardlink / copy / move
    dry_run: bool = True                    # 是否仅为预览
    success: bool = False                   # 是否成功
    error: Optional[str] = None             # 错误信息


@dataclass
class RenameConfig:
    """重命名配置"""
    # 电影命名模板
    movie_template: str = "{title} ({year}) [{quality}]"
    # 电视剧命名模板
    tv_template: str = "{title}/Season {season:02d}/{title} - S{season:02d}E{episode:02d} - [{quality}]"
    # 目录整理模板
    movie_folder: str = "{title} ({year})"
    tv_folder: str = "{title}"
    
    # 文件整理策略
    organize_by: str = "year"               # year / genre / first_letter
    create_hardlink: bool = True            # 默认使用硬链接
    keep_original: bool = False             # 是否保留源文件


@dataclass
class MediaLensConfig:
    """全局配置"""
    tmdb_api_key: str = ""
    tmdb_language: str = "zh-CN"
    
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_api_base: str = ""
    
    tmdb_confidence_threshold: int = 80
    llm_confidence_threshold: int = 60
    
    dry_run: bool = True
    organize_mode: str = "hardlink"  # hardlink / copy / move
    
    db_path: str = "data/medialens.db"
    data_dir: str = "data"
    
    rename: RenameConfig = field(default_factory=RenameConfig)
