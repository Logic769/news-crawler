"""Persistent asynchronous job manager backed by SQLite."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from news_crawler import now_iso
from news_api.db import connect
from news_api.settings import Settings
from news_api.tasks import execute_job
from news_api.timeutil import local_iso

LOG = logging.getLogger("news_api.jobs")

# 已完成作业保留 30 天，避免 crawl_jobs 无界增长；查询去重索引见 SCHEMA_SQL。
JOB_RETENTION_DAYS = 30


class JobQueueFull(RuntimeError):
    """Raised when the number of queued/running jobs reaches the configured cap."""


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "type": row["type"],
        "status": row["status"],
        "request": _decode(row["payload"], {}),
        "result": _decode(row["result"], None),
        "error": row["error"] or None,
        "created_at": local_iso(row["created_at"]),
        "started_at": local_iso(row["started_at"]),
        "finished_at": local_iso(row["finished_at"]),
    }


class JobManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.job_workers),
            thread_name_prefix="news-job",
        )
        self._lock = threading.Lock()
        self._futures: dict[str, Any] = {}
        self._closed = False

    def _connect(self) -> sqlite3.Connection:
        return connect(self._settings.db_path, read_only=False)

    @contextlib.contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def start(self) -> None:
        """Mark interrupted jobs and resume the queued ones."""
        with self._connection() as conn:
            conn.execute(
                "UPDATE crawl_jobs SET status = ?, error = ?, finished_at = ? "
                "WHERE status = ?",
                ("failed", "服务重启，任务已中断", now_iso(), "running"),
            )
            cutoff = (
                dt.datetime.now().astimezone() - dt.timedelta(days=JOB_RETENTION_DAYS)
            ).strftime("%Y-%m-%d %H:%M:%S")
            removed = conn.execute(
                "DELETE FROM crawl_jobs WHERE status IN (?, ?) AND finished_at < ?",
                ("succeeded", "failed", cutoff),
            ).rowcount
            if removed:
                LOG.info("清理 %s 条超过 %s 天的已完成作业", removed, JOB_RETENTION_DAYS)
            rows = conn.execute(
                "SELECT id FROM crawl_jobs WHERE status = ? ORDER BY created_at",
                ("queued",),
            ).fetchall()
            conn.commit()
        for row in rows:
            self.submit(str(row["id"]))

    def create(self, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM crawl_jobs WHERE type = ? AND payload = ? "
                "AND status IN (?, ?) ORDER BY created_at LIMIT 1",
                (job_type, payload_json, "queued", "running"),
            ).fetchone()
            if existing is not None:
                conn.commit()
                job = self.get(str(existing["id"]))
                if job is not None:
                    return job
            pending = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM crawl_jobs WHERE status IN (?, ?)",
                    ("queued", "running"),
                ).fetchone()["n"]
            )
            if pending >= self._settings.max_pending_jobs:
                conn.rollback()
                raise JobQueueFull(
                    f"排队或运行中的任务已达上限 {self._settings.max_pending_jobs}，请稍后重试"
                )
            conn.execute(
                "INSERT INTO crawl_jobs (id, type, status, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, job_type, "queued", payload_json, now_iso()),
            )
            conn.commit()
        self.submit(job_id)
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("任务创建后无法读取")
        return job

    def submit(self, job_id: str) -> None:
        with self._lock:
            if self._closed or job_id in self._futures:
                return
            future = self._executor.submit(self._run, job_id)
            self._futures[job_id] = future

        def _forget(_future: Any, jid: str = job_id) -> None:
            with self._lock:
                self._futures.pop(jid, None)

        future.add_done_callback(_forget)

    def _run(self, job_id: str) -> None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["status"] != "queued":
                return
            claimed = conn.execute(
                "UPDATE crawl_jobs SET status = ?, started_at = ? WHERE id = ? AND status = ?",
                ("running", now_iso(), job_id, "queued"),
            ).rowcount
            conn.commit()
        if not claimed:
            return

        payload = _decode(row["payload"], {})
        try:
            result = execute_job(row["type"], payload, self._settings)
        except BaseException as exc:  # noqa: BLE001 - 包括 KeyboardInterrupt，避免作业卡在 running
            LOG.exception("任务 %s 失败", job_id)
            self._finish(job_id, "failed", error=str(exc)[:2000] or exc.__class__.__name__)
            if not isinstance(exc, Exception):
                raise
        else:
            self._finish(job_id, "succeeded", result=result)

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE crawl_jobs SET status = ?, result = ?, error = ?, finished_at = ? "
                "WHERE id = ?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    now_iso(),
                    job_id,
                ),
            )
            conn.commit()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM crawl_jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def list(
        self,
        *,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if job_type:
            where.append("type = ?")
            params.append(job_type)
        where_sql = (" WHERE " + " AND ".join(where)) if where else ""
        with self._connection() as conn:
            conn.execute("BEGIN")
            total = int(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM crawl_jobs{where_sql}", params
                ).fetchone()["n"]
            )
            rows = conn.execute(
                f"SELECT * FROM crawl_jobs{where_sql} ORDER BY created_at DESC, id DESC "
                "LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            conn.commit()
        return [_row_to_job(row) for row in rows], total

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
