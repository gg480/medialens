"""
配置加载模块

支持从 YAML 配置文件、环境变量（.env 文件）加载配置，
优先级：命令行参数 > 环境变量 > 配置文件 > 默认值
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

from medialens.models import MediaLensConfig, RenameConfig


# 配置项到环境变量名的映射
_ENV_MAP: dict[str, str] = {
    "tmdb_api_key": "TMDB_API_KEY",
    "tmdb_language": "TMDB_LANGUAGE",
    "llm_provider": "LLM_PROVIDER",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "llm_api_base": "LLM_API_BASE",
    "tmdb_confidence_threshold": "TMDB_CONFIDENCE_THRESHOLD",
    "llm_confidence_threshold": "LLM_CONFIDENCE_THRESHOLD",
    "dry_run": "RENAME_DRY_RUN",
    "organize_mode": "ORGANIZE_MODE",
    "db_path": "DB_PATH",
    "data_dir": "DATA_DIR",
}


def _load_env_file(env_path: Optional[str] = None) -> None:
    """加载 .env 文件到环境变量。

    Args:
        env_path: .env 文件路径，None 时自动查找
    """
    if env_path:
        load_dotenv(env_path, override=False)
    else:
        # 从当前目录向上查找 .env
        cwd = Path.cwd()
        for parent in [cwd] + list(cwd.parents):
            dotenv_path = parent / ".env"
            if dotenv_path.exists():
                load_dotenv(dotenv_path, override=False)
                return


def _load_yaml_config(config_path: str) -> dict[str, Any]:
    """从 YAML 文件加载配置。

    Args:
        config_path: YAML 文件路径

    Returns:
        解析后的配置字典，文件不存在时返回空字典
    """
    path = Path(config_path)
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read_env_config() -> dict[str, Any]:
    """从环境变量读取配置。

    只读取 _ENV_MAP 中定义的配置项。

    Returns:
        从环境变量提取的配置字典
    """
    config: dict[str, Any] = {}
    for config_key, env_key in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value is not None:
            # 处理布尔值和数字
            if config_key in ("dry_run",):
                config[config_key] = value.lower() in ("true", "1", "yes")
            elif config_key in ("tmdb_confidence_threshold", "llm_confidence_threshold"):
                config[config_key] = int(value)
            else:
                config[config_key] = value
    return config


def _merge_config(
    yaml_config: dict[str, Any],
    env_config: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """按优先级合并配置。

    优先级：overrides > env_config > yaml_config

    Args:
        yaml_config: 从 YAML 文件加载的配置
        env_config: 从环境变量读取的配置
        overrides: 命令行参数覆盖

    Returns:
        合并后的配置字典
    """
    merged: dict[str, Any] = {}

    # 1. YAML 配置（最低优先级）
    merged.update(yaml_config)

    # 2. 环境变量覆盖
    merged.update(env_config)

    # 3. 命令行覆盖（最高优先级）
    merged.update({k: v for k, v in overrides.items() if v is not None})

    return merged


def _flatten_yaml(yaml_data: dict[str, Any]) -> dict[str, Any]:
    """将嵌套的 YAML 配置展平为 MediaLensConfig 兼容的键。

    例如：
    ```yaml
    tmdb:
      api_key: xxx
      language: zh-CN
    ```
    变成：
    ```python
    {"tmdb_api_key": "xxx", "tmdb_language": "zh-CN"}
    ```

    Args:
        yaml_data: 原始 YAML 数据

    Returns:
        展平后的配置字典
    """
    flattened: dict[str, Any] = {}

    # 直接一级键
    for key, value in yaml_data.items():
        if isinstance(value, dict):
            # 嵌套键：tmdb.api_key
            for sub_key, sub_value in value.items():
                flat_key = f"{key}_{sub_key}"
                flattened[flat_key] = sub_value
        else:
            flattened[key] = value

    return flattened


def _read_env_path() -> str:
    """获取 .env 文件路径。"""
    return os.environ.get("MEDIALENS_ENV_FILE", "")


def generate_default_config(path: str) -> str:
    """生成默认配置文件到指定路径。

    Args:
        path: 配置文件输出路径

    Returns:
        输出文件的绝对路径
    """
    default_config = {
        "tmdb": {
            "api_key": "",
            "language": "zh-CN",
        },
        "llm": {
            "provider": "openai",
            "api_key": "",
            "model": "gpt-4o-mini",
            "api_base": "",
        },
        "thresholds": {
            "tmdb_confidence": 80,
            "llm_confidence": 60,
        },
        "file_management": {
            "dry_run": True,
            "organize_mode": "hardlink",
        },
        "storage": {
            "db_path": "data/medialens.db",
            "data_dir": "data",
        },
    }

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        yaml.dump(default_config, f, default_flow_style=False, allow_unicode=True)

    return str(output.resolve())


def load_config(
    config_path: str = "config.yaml",
    env_file: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> MediaLensConfig:
    """加载并合并配置，返回 MediaLensConfig 对象。

    加载顺序：
    1. 读取 YAML 配置文件（config.yaml）
    2. 读取 .env 文件（自动查找或指定路径）
    3. 读取环境变量
    4. 应用命令行覆盖（overrides）

    Args:
        config_path: YAML 配置文件路径
        env_file: .env 文件路径，None 时自动查找
        overrides: 命令行参数覆盖字典

    Returns:
        合并后的 MediaLensConfig 对象
    """
    # 1. 加载环境变量
    if env_file:
        _load_env_file(env_file)
    else:
        _load_env_file(_read_env_path())

    # 2. 加载 YAML 配置
    yaml_data = _load_yaml_config(config_path)
    yaml_flattened = _flatten_yaml(yaml_data)

    # 3. 读取环境变量
    env_config = _read_env_config()

    # 4. 合并
    merged = _merge_config(yaml_flattened, env_config, overrides or {})

    # 5. 构建 MediaLensConfig
    rename_config = RenameConfig()
    # 从合并配置中读取重命名配置
    if "movie_template" in merged:
        rename_config.movie_template = str(merged["movie_template"])
    if "tv_template" in merged:
        rename_config.tv_template = str(merged["tv_template"])
    if "movie_folder" in merged:
        rename_config.movie_folder = str(merged["movie_folder"])
    if "tv_folder" in merged:
        rename_config.tv_folder = str(merged["tv_folder"])
    if "organize_by" in merged:
        rename_config.organize_by = str(merged["organize_by"])
    if "create_hardlink" in merged:
        rename_config.create_hardlink = bool(merged["create_hardlink"])
    if "keep_original" in merged:
        rename_config.keep_original = bool(merged["keep_original"])

    config = MediaLensConfig(
        tmdb_api_key=str(merged.get("tmdb_api_key", "")),
        tmdb_language=str(merged.get("tmdb_language", "zh-CN")),
        llm_provider=str(merged.get("llm_provider", "openai")),
        llm_api_key=str(merged.get("llm_api_key", "")),
        llm_model=str(merged.get("llm_model", "gpt-4o-mini")),
        llm_api_base=str(merged.get("llm_api_base", "")),
        tmdb_confidence_threshold=int(merged.get("tmdb_confidence_threshold", 80)),
        llm_confidence_threshold=int(merged.get("llm_confidence_threshold", 60)),
        dry_run=bool(merged.get("dry_run", True)),
        organize_mode=str(merged.get("organize_mode", "hardlink")),
        db_path=str(merged.get("db_path", "data/medialens.db")),
        data_dir=str(merged.get("data_dir", "data")),
        rename=rename_config,
    )

    return config
