"""scheduler.py 的离线单元测试。"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scheduler


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "db": "/app/data/news.db",
        "config": "sources.local.json",
        "days": 3,
        "lock_file": "/app/data/.crawl.lock",
        "insight_url": "",
        "insight_api_key": "",
        "analyze_limit": 1000,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ParseTimesTests(unittest.TestCase):
    def test_parses_sorts_and_deduplicates(self) -> None:
        self.assertEqual(
            scheduler.parse_times("15:00, 09:00,09:00"),
            [dt.time(9, 0), dt.time(15, 0)],
        )

    def test_rejects_invalid_time(self) -> None:
        for raw in ("09:00,25:00", "09:00,abc", "09:60"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    scheduler.parse_times(raw)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            scheduler.parse_times(" , ")


class NextRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.times = [dt.time(9, 0), dt.time(15, 0)]
        self.tz = dt.timezone(dt.timedelta(hours=8))

    def test_before_first_run_same_day(self) -> None:
        now = dt.datetime(2026, 11, 17, 8, 30, tzinfo=self.tz)
        self.assertEqual(
            scheduler.next_run_at(now, self.times),
            dt.datetime(2026, 11, 17, 9, 0, tzinfo=self.tz),
        )

    def test_between_runs_same_day(self) -> None:
        now = dt.datetime(2026, 11, 17, 12, 0, tzinfo=self.tz)
        self.assertEqual(
            scheduler.next_run_at(now, self.times),
            dt.datetime(2026, 11, 17, 15, 0, tzinfo=self.tz),
        )

    def test_after_last_run_next_day(self) -> None:
        now = dt.datetime(2026, 11, 17, 15, 0, 1, tzinfo=self.tz)
        self.assertEqual(
            scheduler.next_run_at(now, self.times),
            dt.datetime(2026, 11, 18, 9, 0, tzinfo=self.tz),
        )

    def test_exactly_at_run_returns_next(self) -> None:
        now = dt.datetime(2026, 11, 17, 9, 0, tzinfo=self.tz)
        self.assertEqual(
            scheduler.next_run_at(now, self.times),
            dt.datetime(2026, 11, 17, 15, 0, tzinfo=self.tz),
        )


class CrawlCommandTests(unittest.TestCase):
    def test_builds_expected_command(self) -> None:
        command = scheduler.crawl_command(_args())
        self.assertEqual(command[:5], [sys.executable, "-X", "utf8", mock.ANY, "run"])
        self.assertIn("--db", command)
        self.assertIn("/app/data/news.db", command)
        self.assertIn("--config", command)
        self.assertIn("sources.local.json", command)
        self.assertIn("--days", command)
        self.assertIn("3", command)
        self.assertTrue(command[3].endswith("news_crawler.py"))


class FileLockTests(unittest.TestCase):
    def test_second_lock_fails_until_released(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "crawl.lock"
            first = scheduler.FileLock(lock_path)
            second = scheduler.FileLock(lock_path)
            self.assertTrue(first.acquire())
            try:
                self.assertFalse(second.acquire())
            finally:
                first.release()
            self.assertTrue(second.acquire())
            second.release()


class ExecuteOnceTests(unittest.TestCase):
    def test_skips_when_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = _args(lock_file=str(Path(temp_dir) / "crawl.lock"))
            holder = scheduler.FileLock(Path(args.lock_file))
            self.assertTrue(holder.acquire())
            try:
                with mock.patch.object(scheduler.subprocess, "run") as run:
                    self.assertEqual(scheduler.execute_once(args), 1)
                    run.assert_not_called()
            finally:
                holder.release()

    def test_success_triggers_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = _args(lock_file=str(Path(temp_dir) / "crawl.lock"), insight_url="http://insight:8020")
            with mock.patch.object(scheduler.subprocess, "run") as run, mock.patch.object(
                scheduler, "trigger_analysis"
            ) as trigger:
                run.return_value = mock.Mock(returncode=0)
                self.assertEqual(scheduler.execute_once(args), 0)
                trigger.assert_called_once_with("http://insight:8020", "", 1000)

    def test_failure_does_not_trigger_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            args = _args(lock_file=str(Path(temp_dir) / "crawl.lock"), insight_url="http://insight:8020")
            with mock.patch.object(scheduler.subprocess, "run") as run, mock.patch.object(
                scheduler, "trigger_analysis"
            ) as trigger:
                run.return_value = mock.Mock(returncode=1)
                self.assertEqual(scheduler.execute_once(args), 1)
                trigger.assert_not_called()


if __name__ == "__main__":
    unittest.main()
