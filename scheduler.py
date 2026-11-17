"""定时采集调度器。

每天在 configurable 的时间点（默认 09:00 / 15:00，Asia/Shanghai）执行一次
news_crawler.py run，成功后可选地触发 news-insight 的分析作业。
只用标准库，避免为定时任务引入额外依赖。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

LOG = logging.getLogger("scheduler")

DEFAULT_TIMES = "09:00,15:00"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_DAYS = 3


def parse_times(raw: str) -> list[dt.time]:
    """解析 HH:MM 列表，去重并按时间升序返回。"""
    times: list[dt.time] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            hour_text, minute_text = value.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
            times.append(dt.time(hour=hour, minute=minute))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"非法时间点: {value!r}，应为 HH:MM") from exc
    if not times:
        raise ValueError("至少需要一个时间点")
    return sorted(set(times))


def next_run_at(now: dt.datetime, times: list[dt.time]) -> dt.datetime:
    """返回严格晚于 now 的下一个触发时刻（保持 now 的时区）。"""
    ordered = sorted(set(times))
    for item in ordered:
        candidate = now.replace(hour=item.hour, minute=item.minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    tomorrow = now.date() + dt.timedelta(days=1)
    first = ordered[0]
    return dt.datetime.combine(tomorrow, first, tzinfo=now.tzinfo)


def crawl_command(args: argparse.Namespace) -> list[str]:
    """构造抓取命令，便于测试而不真正启动子进程。"""
    script = Path(__file__).resolve().parent / "news_crawler.py"
    return [
        sys.executable,
        "-X",
        "utf8",
        str(script),
        "run",
        "--db",
        str(args.db),
        "--config",
        str(args.config),
        "--days",
        str(args.days),
    ]


def trigger_analysis(url: str, api_key: str, limit: int = 1000, timeout: int = 15) -> bool:
    """采集成功后提交 insight 分析作业；失败只记日志，不影响下一轮采集。"""
    if not url:
        return False
    payload = json.dumps({"type": "analyze", "limit": limit}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(
        url.rstrip("/") + "/v1/jobs",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(500).decode("utf-8", "replace")
        LOG.info("已触发 insight 分析作业: HTTP %s %s", response.status, body)
        return True
    except Exception as exc:  # noqa: BLE001 - 辅助链路失败不阻断采集
        LOG.warning("触发 insight 分析失败: %s", exc)
        return False


class FileLock:
    """跨平台的单实例锁；拿不到锁时返回 False。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Windows 的 msvcrt 锁要求文件至少有一个字节可锁；先补一个占位字节。
        self._file = open(self.path, "a+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"0")
            self._file.flush()
        self._file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._file.seek(0)
            self._file.truncate()
            self._file.write(str(os.getpid()).encode("ascii"))
            self._file.flush()
            return True
        except OSError:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
            return False

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


def execute_once(args: argparse.Namespace) -> int:
    lock = FileLock(Path(args.lock_file))
    if not lock.acquire():
        LOG.warning("已有采集任务在运行，跳过本轮")
        return 1
    try:
        LOG.info("开始抓取: %s", " ".join(crawl_command(args)))
        code = subprocess.run(crawl_command(args), check=False).returncode
        LOG.info("抓取结束，exit code=%s", code)
        if code == 0:
            trigger_analysis(args.insight_url, args.insight_api_key, args.analyze_limit)
        return code
    finally:
        lock.release()


def run_forever(args: argparse.Namespace) -> int:
    times = parse_times(args.times)
    timezone = ZoneInfo(args.timezone)
    stop = False

    def _stop(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    LOG.info("定时采集启动: times=%s timezone=%s", args.times, args.timezone)
    while not stop:
        now = dt.datetime.now(timezone)
        at = next_run_at(now, times)
        LOG.info("下一次采集: %s", at.isoformat())
        while not stop:
            remaining = (at - dt.datetime.now(timezone)).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))
        if stop:
            break
        execute_once(args)
    LOG.info("定时采集已停止")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="news-crawler 定时采集调度器")
    parser.add_argument("--once", action="store_true", help="立即执行一次后退出，便于测试")
    parser.add_argument("--times", default=os.getenv("SCHEDULE_TIMES", DEFAULT_TIMES))
    parser.add_argument("--timezone", default=os.getenv("SCHEDULE_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--days", type=int, default=int(os.getenv("CRAWL_DAYS", DEFAULT_DAYS)))
    parser.add_argument("--config", default=os.getenv("CRAWL_CONFIG", "sources.local.json"))
    parser.add_argument("--db", default=os.getenv("CRAWL_DB", "/app/data/news.db"))
    parser.add_argument("--lock-file", default=os.getenv("CRAWL_LOCK_FILE", "/app/data/.crawl.lock"))
    parser.add_argument("--insight-url", default=os.getenv("INSIGHT_URL", ""))
    parser.add_argument("--insight-api-key", default=os.getenv("INSIGHT_API_KEY", ""))
    parser.add_argument("--analyze-limit", type=int, default=int(os.getenv("ANALYZE_LIMIT", "1000")))
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    args = build_parser().parse_args()
    parse_times(args.times)  # 启动即校验，配置错误直接退出
    if args.once:
        return execute_once(args)
    return run_forever(args)


if __name__ == "__main__":
    raise SystemExit(main())
