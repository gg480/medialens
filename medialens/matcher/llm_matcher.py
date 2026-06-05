"""
MediaLens LLM 补充裁决模块

TMDB 置信度低于阈值时，LLM 从 TMDB 候选列表中智能选择最匹配项。
LLM 是"补充"角色，不是"主决策"角色——只从已有候选中选择，不创造新信息。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from medialens.models import (
    MatchResult,
    MatchSource,
    MediaCandidate,
    ParsedMedia,
)
from medialens.storage.database import DatabaseManager


class LlmMatcher:
    """
    LLM 补充裁决器。

    当 TMDB 模糊匹配置信度低于阈值时，将文件名 + TMDB 候选列表提交给 LLM，
    由 LLM 从候选中选择最匹配的媒体项。

    支持通过 litellm 统一切换 provider（openai / anthropic / ollama）。
    """

    def __init__(
        self,
        provider: str = "openai",
        api_key: str = "",
        model: str = "gpt-4o-mini",
        api_base: str = "",
        db: Optional[DatabaseManager] = None,
    ) -> None:
        """
        初始化 LLM 匹配器。

        Args:
            provider: LLM 提供商（openai / anthropic / ollama）
            api_key: API 密钥
            model: 模型名（ollama 格式: "ollama/qwen2.5"）
            api_base: 自定义 API 地址
            db: 可选的 DatabaseManager，用于记录 LLM 调用日志
        """
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.api_base = api_base
        self.db = db

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def verify(
        self,
        parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> Optional[MediaCandidate]:
        """
        对 TMDB 候选列表进行 LLM 裁决。

        流程：
        1. 构建 prompt（含文件名、解析结果、候选列表）
        2. 调用 LLM API
        3. 解析 LLM 响应，提取选择的候选
        4. 返回最佳候选（或 None）

        Args:
            parsed: 解析后的媒体信息
            candidates: TMDB 搜索候选列表

        Returns:
            选中的 MediaCandidate，或 None（无法确定时）
        """
        if not self.is_available():
            return None

        if not candidates:
            return None

        prompt = self.build_prompt(parsed, candidates)
        start_time = time.monotonic()

        try:
            response_text = self._call_llm(prompt)
        except Exception as exc:
            self._log_call(
                file_path=str(parsed.raw_path),
                prompt=prompt,
                response=f"error: {exc}",
                success=False,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )
            return None

        duration_ms = int((time.monotonic() - start_time) * 1000)
        self._log_call(
            file_path=str(parsed.raw_path),
            prompt=prompt,
            response=response_text,
            success=True,
            duration_ms=duration_ms,
        )

        return self.parse_response(response_text, candidates)

    def build_prompt(
        self,
        parsed: ParsedMedia,
        candidates: list[MediaCandidate],
    ) -> str:
        """
        构造 LLM Prompt，要求 LLM 从候选列表中选择最匹配的媒体。

        Args:
            parsed: 解析后的媒体信息
            candidates: TMDB 搜索候选列表

        Returns:
            构造好的 prompt 字符串
        """
        candidate_lines: list[str] = []
        for i, c in enumerate(candidates, start=1):
            year_str = str(c.year) if c.year else "未知年份"
            overview_snippet = (c.overview or "")[:100].replace("\n", " ")
            candidate_lines.append(
                f"{i}. {c.title} ({year_str}) - {overview_snippet}"
            )

        # 剧集附加信息
        episode_info = ""
        if parsed.is_episode or parsed.season is not None:
            season = parsed.season if parsed.season is not None else "?"
            episode = parsed.episode if parsed.episode is not None else "?"
            episode_info = f"\n季: {season}, 集: {episode}"

        prompt = f"""你是一个专业的媒体文件识别助手。你的任务是从候选列表中选择最匹配的文件名对应的媒体信息。

## 待识别文件
文件名: {parsed.raw_filename}
解析标题: {parsed.title or "未知"}
解析年份: {parsed.year if parsed.year else "未知"}
文件格式: {parsed.file_format.value}{episode_info}

## 候选列表
{chr(10).join(candidate_lines)}

## 要求
1. 如果文件名和某个候选的标题+年份高度匹配，选择该候选
2. 如果文件名包含中文，优先匹配中文标题
3. 如果都不匹配，回答 "none"
4. 只返回 JSON 格式：{{"selected": 序号, "confidence": 0-100, "reason": "原因"}}
5. 置信度低于 50 时，selected 返回 -1"""

        return prompt

    def parse_response(
        self,
        response: str,
        candidates: list[MediaCandidate],
    ) -> Optional[MediaCandidate]:
        """
        解析 LLM 的 JSON 响应，返回选择的候选。

        Args:
            response: LLM 返回的原始文本
            candidates: 原始候选列表（用于索引查找）

        Returns:
            选中的 MediaCandidate，或 None
        """
        try:
            # 尝试从响应中提取 JSON 块（可能被 ```json ... ``` 包裹）
            cleaned = response.strip()
            if "```json" in cleaned:
                # 提取 ```json ... ``` 之间的内容
                start = cleaned.index("```json") + 7
                end = cleaned.index("```", start)
                cleaned = cleaned[start:end].strip()
            elif "```" in cleaned:
                start = cleaned.index("```") + 3
                end = cleaned.index("```", start)
                cleaned = cleaned[start:end].strip()

            data: dict[str, Any] = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None

        selected = data.get("selected", -1)
        # 兼容数字字符串类型（如 "1"）
        if isinstance(selected, str):
            try:
                selected = int(selected)
            except (ValueError, TypeError):
                return None
        if not isinstance(selected, (int, float)):
            return None

        idx = int(selected)
        # 索引从 1 开始（prompt 中的序号）
        if idx < 1 or idx > len(candidates):
            return None

        candidate = candidates[idx - 1]
        confidence = data.get("confidence", 0)
        if isinstance(confidence, (int, float)):
            candidate.match_score = float(confidence)

        return candidate

    def is_available(self) -> bool:
        """检查 LLM 是否配置可用。"""
        # ollama 可以不用 api_key，但必须有模型名
        if self.provider == "ollama":
            return bool(self.model)
        # 其他 provider 需要 api_key
        if not self.api_key:
            return False
        return True

    @staticmethod
    def get_llm_confidence(result: MatchResult) -> float:
        """
        如果 LLM 参与了匹配，返回 LLM 结果的置信度。

        Args:
            result: 匹配结果

        Returns:
            置信度值（0-100），如果非 LLM 来源则返回 0.0
        """
        if result.source != MatchSource.LLM_VERIFIED:
            return 0.0
        return result.confidence

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """
        调用 LLM API，返回响应文本。

        Raises:
            RuntimeError: API 调用失败时抛出
        """
        try:
            import litellm
        except ImportError:
            raise RuntimeError(
                "litellm 未安装，请执行: pip install litellm"
            ) from None

        # 构建模型全名
        if self.provider == "ollama" and not self.model.startswith("ollama/"):
            model_name = f"ollama/{self.model}"
        elif self.provider == "anthropic" and not self.model.startswith("claude"):
            model_name = f"anthropic/{self.model}"
        else:
            model_name = self.model

        messages: list[dict[str, str]] = [
            {
                "role": "user",
                "content": prompt,
            }
        ]

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.1,  # 低温度，提高确定性
            "max_tokens": 512,
            "timeout": 30,  # 30 秒超时
        }

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        try:
            resp = litellm.completion(**kwargs)
            text: str = resp.choices[0].message.content or ""
            return text.strip()
        except Exception as exc:
            raise RuntimeError(f"LLM 调用失败: {exc}") from exc

    def _log_call(
        self,
        file_path: str,
        prompt: str,
        response: str,
        success: bool,
        duration_ms: int,
    ) -> None:
        """记录 LLM 调用日志到数据库（如果已配置 db）。"""
        if self.db is None:
            return

        try:
            self.db.log_llm_call(
                file_path=file_path,
                prompt=prompt,
                response=response,
                model=self.model,
                tokens_in=0,   # litellm 原生不暴露 token 数（简化处理）
                tokens_out=0,
                duration_ms=duration_ms,
                success=success,
            )
        except Exception:
            pass  # 日志失败不影响主流程
