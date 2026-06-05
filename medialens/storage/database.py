"""
MediaLens SQLite 存储层

提供匹配记录、LLM 调用日志、文件操作日志的持久化。
所有写操作使用事务保证原子性。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from medialens.models import (
    FileFormat,
    MatchResult,
    MatchSource,
    MediaType,
    ParsedMedia,
)


class DatabaseManager:
    """SQLite 数据库管理器，封装 match_history / llm_call_log / file_operations 表的 CRUD。"""

    def __init__(self, db_path: str = "data/medialens.db") -> None:
        self.db_path = str(Path(db_path).resolve())
        # 确保父目录存在
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        """获取或创建数据库连接（线程安全延迟初始化）。"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,  # 由外部锁保证线程安全
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self) -> None:
        """创建表结构（幂等）。"""
        with self._lock:
            c = self.conn.cursor()
            c.executescript(SCHEMA_SQL)
            c.executescript(CHECKPOINT_SCHEMA_SQL)
            self.conn.commit()

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------
    # match_history CRUD
    # ------------------------------------------------------------------

    def save_match(
        self,
        parsed: ParsedMedia,
        result: MatchResult,
        file_hash: str = "",
    ) -> int:
        """
        保存匹配记录到 match_history 表。

        返回新记录的自增 id。
        如果 file_path 已存在，执行 INSERT OR REPLACE（UPSERT）。
        """
        values: dict[str, Any] = {
            "file_path": str(parsed.raw_path),
            "file_hash": file_hash,
            "file_format": parsed.file_format.value,
            "parsed_title": parsed.title,
            "parsed_year": parsed.year,
            "parsed_season": parsed.season,
            "parsed_episode": parsed.episode,
            "tmdb_id": result.tmdb_id if result.matched else None,
            "media_type": result.media_type.value,
            "matched_title": result.title,
            "matched_year": result.year,
            "match_source": result.source.value,
            "confidence": result.confidence,
            "overview": result.overview or "",
            "poster_path": result.poster_path,
            "backdrop_path": result.backdrop_path,
            "llm_raw_response": result.raw_response,
        }

        sql = """
            INSERT INTO match_history (
                file_path, file_hash, file_format,
                parsed_title, parsed_year, parsed_season, parsed_episode,
                tmdb_id, media_type, matched_title, matched_year,
                match_source, confidence,
                overview, poster_path, backdrop_path,
                llm_raw_response
            ) VALUES (
                :file_path, :file_hash, :file_format,
                :parsed_title, :parsed_year, :parsed_season, :parsed_episode,
                :tmdb_id, :media_type, :matched_title, :matched_year,
                :match_source, :confidence,
                :overview, :poster_path, :backdrop_path,
                :llm_raw_response
            )
            ON CONFLICT(file_path) DO UPDATE SET
                file_hash          = excluded.file_hash,
                file_format        = excluded.file_format,
                parsed_title       = excluded.parsed_title,
                parsed_year        = excluded.parsed_year,
                parsed_season      = excluded.parsed_season,
                parsed_episode     = excluded.parsed_episode,
                tmdb_id            = excluded.tmdb_id,
                media_type         = excluded.media_type,
                matched_title      = excluded.matched_title,
                matched_year       = excluded.matched_year,
                match_source       = excluded.match_source,
                confidence         = excluded.confidence,
                overview           = excluded.overview,
                poster_path        = excluded.poster_path,
                backdrop_path      = excluded.backdrop_path,
                llm_raw_response   = excluded.llm_raw_response,
                updated_at         = CURRENT_TIMESTAMP
        """

        with self._lock:
            c = self.conn.cursor()
            c.execute(sql, values)
            self.conn.commit()
            return c.lastrowid  # type: ignore[return-value]

    def get_match_by_path(self, file_path: str) -> Optional[dict[str, Any]]:
        """根据文件路径查询匹配记录。"""
        return self._query_one(
            "SELECT * FROM match_history WHERE file_path = ?", (file_path,)
        )

    def get_match_by_hash(self, file_hash: str) -> Optional[dict[str, Any]]:
        """根据文件哈希查询（快速缓存命中）。"""
        return self._query_one(
            "SELECT * FROM match_history WHERE file_hash = ?", (file_hash,)
        )

    def get_match_by_id(self, match_id: int) -> Optional[dict[str, Any]]:
        """根据自增 id 查询匹配记录。"""
        return self._query_one(
            "SELECT * FROM match_history WHERE id = ?", (match_id,)
        )

    def update_match(self, match_id: int, updates: dict[str, Any]) -> bool:
        """
        更新匹配记录（用于用户纠正后更新）。

        只更新传入的字段，自动设置 updated_at。
        返回是否找到并更新了记录。
        """
        if not updates:
            return False

        allowed_keys = {
            "file_hash", "file_format",
            "parsed_title", "parsed_year", "parsed_season", "parsed_episode",
            "tmdb_id", "media_type", "matched_title", "matched_year",
            "match_source", "confidence",
            "overview", "poster_path", "backdrop_path",
            "llm_raw_response", "llm_model",
        }
        # 只保留允许更新的字段
        filtered = {k: v for k, v in updates.items() if k in allowed_keys}
        if not filtered:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [match_id]

        sql = f"UPDATE match_history SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"

        with self._lock:
            c = self.conn.cursor()
            c.execute(sql, values)
            self.conn.commit()
            return c.rowcount > 0

    def delete_match(self, match_id: int) -> bool:
        """删除匹配记录。返回是否找到并删除了记录。"""
        with self._lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM match_history WHERE id = ?", (match_id,))
            self.conn.commit()
            return c.rowcount > 0

    # ------------------------------------------------------------------
    # LLM 调用日志
    # ------------------------------------------------------------------

    def log_llm_call(
        self,
        file_path: str,
        prompt: str,
        response: str,
        model: str,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
        success: bool,
    ) -> int:
        """记录 LLM 调用日志，返回记录 id。"""
        values = {
            "file_path": file_path,
            "prompt": prompt,
            "response": response,
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_ms": duration_ms,
            "success": 1 if success else 0,
        }

        sql = """
            INSERT INTO llm_call_log (
                file_path, prompt, response, model,
                tokens_in, tokens_out, duration_ms, success
            ) VALUES (
                :file_path, :prompt, :response, :model,
                :tokens_in, :tokens_out, :duration_ms, :success
            )
        """

        with self._lock:
            c = self.conn.cursor()
            c.execute(sql, values)
            self.conn.commit()
            return c.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 文件操作日志
    # ------------------------------------------------------------------

    def log_file_operation(
        self,
        source: str,
        target: str,
        operation: str,
        dry_run: bool,
        success: bool,
        error: str = "",
        match_id: Optional[int] = None,
    ) -> int:
        """记录文件操作，返回记录 id。"""
        values = {
            "source_path": source,
            "target_path": target,
            "operation": operation,
            "dry_run": 1 if dry_run else 0,
            "success": 1 if success else 0,
            "error": error,
            "match_history_id": match_id,
        }

        sql = """
            INSERT INTO file_operations (
                source_path, target_path, operation,
                dry_run, success, error, match_history_id
            ) VALUES (
                :source_path, :target_path, :operation,
                :dry_run, :success, :error, :match_history_id
            )
        """

        with self._lock:
            c = self.conn.cursor()
            c.execute(sql, values)
            self.conn.commit()
            return c.lastrowid  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """返回统计信息。

        返回字段与前端期望格式兼容：
        - total_matches: 总匹配数
        - cache_hits: 缓存命中数
        - llm_calls: LLM 调用次数
        - files_organized: 文件整理次数（成功的操作）
        - cache_rate: 缓存命中率百分比
        - llm_rate: LLM 调用占比百分比
        - recent_activity: 最近 24 小时活动数
        """
        with self._lock:
            c = self.conn.cursor()

            total_matches = c.execute(
                "SELECT COUNT(*) FROM match_history"
            ).fetchone()[0]

            cache_hits = c.execute(
                "SELECT COUNT(*) FROM match_history WHERE match_source = ?",
                (MatchSource.CACHE_HIT.value,),
            ).fetchone()[0]

            source_distribution: dict[str, int] = {}
            rows = c.execute(
                "SELECT match_source, COUNT(*) as cnt FROM match_history "
                "WHERE match_source IS NOT NULL GROUP BY match_source"
            ).fetchall()
            for row in rows:
                source_distribution[row["match_source"]] = row["cnt"]

            llm_call_count = c.execute(
                "SELECT COUNT(*) FROM llm_call_log"
            ).fetchone()[0]

            llm_success = c.execute(
                "SELECT COUNT(*) FROM llm_call_log WHERE success = 1"
            ).fetchone()[0]

            file_op_count = c.execute(
                "SELECT COUNT(*) FROM file_operations"
            ).fetchone()[0]

            file_op_success = c.execute(
                "SELECT COUNT(*) FROM file_operations WHERE success = 1"
            ).fetchone()[0]

            # 平均置信度
            avg_conf_row = c.execute(
                "SELECT AVG(confidence) FROM match_history WHERE confidence > 0"
            ).fetchone()
            avg_confidence = round(avg_conf_row[0], 2) if avg_conf_row[0] else 0.0

            # 缓存命中率
            cache_hit_rate = round(cache_hits / total_matches, 4) if total_matches > 0 else 0.0

            # 最近 24 小时活动数（三张表的操作合计）
            recent = c.execute(
                "SELECT ("
                "  (SELECT COUNT(*) FROM match_history WHERE created_at >= datetime('now', '-1 day')) + "
                "  (SELECT COUNT(*) FROM llm_call_log WHERE created_at >= datetime('now', '-1 day')) + "
                "  (SELECT COUNT(*) FROM file_operations WHERE created_at >= datetime('now', '-1 day'))"
                ")"
            ).fetchone()[0]

        return {
            "total_matches": total_matches,
            "cache_hits": cache_hits,
            "llm_calls": llm_call_count,
            "files_organized": file_op_success,
            "cache_rate": round(cache_hit_rate * 100, 1),
            "llm_rate": round(llm_call_count / total_matches * 100, 1) if total_matches > 0 else 0.0,
            "recent_activity": recent,
            "cache_hit_rate": cache_hit_rate,
            "source_distribution": source_distribution,
            "llm_success_count": llm_success,
            "file_operation_count": file_op_count,
            "avg_confidence": avg_confidence,
        }

    # ------------------------------------------------------------------
    # 辅助查询：最近匹配、日志、待处理文件
    # ------------------------------------------------------------------

    def get_recent_matches(self, limit: int = 10) -> list[dict[str, Any]]:
        """查询最近的匹配记录。

        Args:
            limit: 返回条数限制

        Returns:
            按 created_at 降序排列的匹配记录列表
        """
        return self._query_all(
            "SELECT * FROM match_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def get_logs(self, level: str = "all", limit: int = 50) -> list[dict[str, Any]]:
        """获取活动日志（合并 LLM 调用日志和文件操作日志）。

        Args:
            level: 过滤级别（all/info/success/warn/error）
            limit: 返回条数限制

        Returns:
            统一格式的日志列表，每条包含 time/level/message 字段
        """
        logs: list[dict[str, Any]] = []

        # 收集 LLM 调用日志
        with self._lock:
            rows = self.conn.cursor().execute(
                "SELECT created_at, success, file_path, model, tokens_in, tokens_out "
                "FROM llm_call_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        for row in rows:
            success = bool(row["success"])
            log_level = "success" if success else "error"
            logs.append({
                "time": row["created_at"],
                "level": log_level,
                "message": (
                    f"{'✓' if success else '✗'} LLM 调用 [{row['model']}] "
                    f"tokens={row['tokens_in']}+{row['tokens_out']} "
                    f"文件: {row['file_path']}"
                ),
            })

        # 收集文件操作日志
        with self._lock:
            rows = self.conn.cursor().execute(
                "SELECT created_at, success, operation, source_path, target_path, error "
                "FROM file_operations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        for row in rows:
            success = bool(row["success"])
            if success:
                log_level = "success"
                logs.append({
                    "time": row["created_at"],
                    "level": log_level,
                    "message": f"✓ {row['operation']}: {row['source_path']} → {row['target_path']}",
                })
            else:
                log_level = "error"
                logs.append({
                    "time": row["created_at"],
                    "level": log_level,
                    "message": f"✗ {row['operation']} 失败: {row['error'] or '未知错误'}",
                })

        # 收集匹配记录作为 info 日志
        with self._lock:
            rows = self.conn.cursor().execute(
                "SELECT created_at, file_path, matched_title, match_source, confidence "
                "FROM match_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        for row in rows:
            source = row["match_source"] or "unknown"
            title = row["matched_title"] or row["file_path"]
            logs.append({
                "time": row["created_at"],
                "level": "info",
                "message": (
                    f"ℹ {row['file_path']} → {title} "
                    f"[来源: {source}, 置信度: {row['confidence']:.0f}%]"
                ),
            })

        # 按时间降序排列、筛选级别、截取数量
        logs.sort(key=lambda x: x.get("time") or "", reverse=True)

        if level != "all":
            logs = [log for log in logs if log["level"] == level]

        return logs[:limit]

    def get_pending_files(
        self, scanned_files: list[str],
    ) -> list[str]:
        """从扫描结果中过滤出已匹配的文件（排除已匹配的）。

        Args:
            scanned_files: 扫描到的所有文件路径列表

        Returns:
            未匹配的文件路径列表
        """
        if not scanned_files:
            return []

        pending: list[str] = []
        with self._lock:
            c = self.conn.cursor()
            for file_path in scanned_files:
                row = c.execute(
                    "SELECT 1 FROM match_history WHERE file_path = ? LIMIT 1",
                    (file_path,),
                ).fetchone()
                if row is None:
                    pending.append(file_path)
        return pending

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 批量任务 checkpoint 支持
    # ------------------------------------------------------------------

    def init_checkpoint_tables(self) -> None:
        """创建 batch_checkpoints 和 batch_jobs 表（幂等）。"""
        with self._lock:
            c = self.conn.cursor()
            c.executescript(CHECKPOINT_SCHEMA_SQL)
            self.conn.commit()

    def save_checkpoint(
        self,
        job_id: str,
        file_path: str,
        status: str,
        result_json: Optional[str] = None,
    ) -> None:
        """保存单个文件的处理进度。

        Args:
            job_id: 任务ID
            file_path: 文件路径
            status: pending/processing/done/failed
            result_json: 可选的JSON结果
        """
        sql = """
            INSERT INTO batch_checkpoints (job_id, file_path, status, result_json, started_at, completed_at)
            VALUES (?, ?, ?, ?,
                CASE WHEN ? = 'processing' THEN CURRENT_TIMESTAMP ELSE NULL END,
                CASE WHEN ? IN ('done', 'failed') THEN CURRENT_TIMESTAMP ELSE NULL END
            )
            ON CONFLICT(job_id, file_path) DO UPDATE SET
                status = excluded.status,
                result_json = excluded.result_json,
                started_at = CASE WHEN excluded.status = 'processing' AND batch_checkpoints.started_at IS NULL
                    THEN CURRENT_TIMESTAMP ELSE batch_checkpoints.started_at END,
                completed_at = CASE WHEN excluded.status IN ('done', 'failed')
                    THEN CURRENT_TIMESTAMP ELSE batch_checkpoints.completed_at END
        """
        with self._lock:
            self.conn.execute(sql, (job_id, file_path, status, result_json, status, status))
            self.conn.commit()

    def load_checkpoint(self, job_id: str) -> list[dict[str, Any]]:
        """加载任务的 checkpoint 进度。

        Returns:
            每个文件一条记录，包含 file_path, status, result_json
        """
        return self._query_all(
            "SELECT file_path, status, result_json, started_at, completed_at "
            "FROM batch_checkpoints WHERE job_id = ? ORDER BY file_path",
            (job_id,),
        )

    def create_job(self, job_id: str, total_files: int, options: dict[str, Any]) -> int:
        """创建批量任务记录（如已存在则忽略）。

        Args:
            job_id: 任务ID
            total_files: 总文件数
            options: 任务选项JSON

        Returns:
            数据库自增id，如已存在则返回0
        """
        import json
        options_str = json.dumps(options, ensure_ascii=False)
        with self._lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO batch_jobs (job_id, total_files, options_json) VALUES (?, ?, ?)",
                (job_id, total_files, options_str),
            )
            self.conn.commit()
            return c.lastrowid or 0  # type: ignore[return-value]

    def update_job_status(
        self, job_id: str, status: str, completed_files: Optional[int] = None,
    ) -> None:
        """更新任务状态。

        Args:
            job_id: 任务ID
            status: running/paused/completed/failed
            completed_files: 已完成文件数（可选）
        """
        if completed_files is not None:
            sql = """
                UPDATE batch_jobs SET status = ?, completed_files = ?,
                    completed_at = CASE WHEN ? IN ('completed', 'failed')
                        THEN CURRENT_TIMESTAMP ELSE completed_at END
                WHERE job_id = ?
            """
            with self._lock:
                self.conn.execute(sql, (status, completed_files, status, job_id))
                self.conn.commit()
        else:
            sql = """
                UPDATE batch_jobs SET status = ?,
                    completed_at = CASE WHEN ? IN ('completed', 'failed')
                        THEN CURRENT_TIMESTAMP ELSE completed_at END
                WHERE job_id = ?
            """
            with self._lock:
                self.conn.execute(sql, (status, status, job_id))
                self.conn.commit()

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有批量任务记录。"""
        return self._query_all(
            "SELECT * FROM batch_jobs ORDER BY started_at DESC"
        )

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[dict[str, Any]]:
        """执行查询，返回单条记录（dict）或 None。"""
        with self._lock:
            c = self.conn.cursor()
            row = c.execute(sql, params).fetchone()
            if row is None:
                return None
            return dict(row)

    def _query_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """执行查询，返回所有记录列表。"""
        with self._lock:
            c = self.conn.cursor()
            rows = c.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


# ------------------------------------------------------------------
# DDL
# ------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS match_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL UNIQUE,
    file_hash TEXT,
    file_format TEXT NOT NULL,

    parsed_title TEXT,
    parsed_year INTEGER,
    parsed_season INTEGER,
    parsed_episode INTEGER,

    tmdb_id INTEGER,
    media_type TEXT,
    matched_title TEXT,
    matched_year INTEGER,
    match_source TEXT,
    confidence REAL DEFAULT 0.0,

    overview TEXT,
    poster_path TEXT,
    backdrop_path TEXT,

    llm_raw_response TEXT,
    llm_model TEXT,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT,
    prompt TEXT,
    response TEXT,
    model TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    success BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS file_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    operation TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    dry_run BOOLEAN DEFAULT 1,
    success BOOLEAN DEFAULT 0,
    error TEXT,
    match_history_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (match_history_id) REFERENCES match_history(id)
);

-- 索引：加速常见查询
CREATE INDEX IF NOT EXISTS idx_match_history_file_hash
    ON match_history(file_hash);
CREATE INDEX IF NOT EXISTS idx_match_history_match_source
    ON match_history(match_source);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_file_path
    ON llm_call_log(file_path);
CREATE INDEX IF NOT EXISTS idx_file_operations_match_history_id
    ON file_operations(match_history_id);

-- 批量任务 checkpoint 表
CREATE TABLE IF NOT EXISTS batch_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(job_id, file_path)
);

CREATE TABLE IF NOT EXISTS batch_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    total_files INTEGER DEFAULT 0,
    completed_files INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    options_json TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_batch_checkpoints_job_id
    ON batch_checkpoints(job_id);
CREATE INDEX IF NOT EXISTS idx_batch_checkpoints_status
    ON batch_checkpoints(status);
"""

CHECKPOINT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS batch_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    UNIQUE(job_id, file_path)
);

CREATE TABLE IF NOT EXISTS batch_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT UNIQUE NOT NULL,
    total_files INTEGER DEFAULT 0,
    completed_files INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    options_json TEXT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_batch_checkpoints_job_id
    ON batch_checkpoints(job_id);
CREATE INDEX IF NOT EXISTS idx_batch_checkpoints_status
    ON batch_checkpoints(status);
"""
