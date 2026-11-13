"""Testes de seleção do backup GitHub quando o disparador externo falha."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts.check_backup import (
    PRIMARY_TITLE,
    backup_needed,
    has_recent_primary_success,
    parse_github_timestamp,
)


NOW = datetime(2026, 11, 13, 23, 50, 0, tzinfo=timezone.utc)


def run(*, minutes_ago=5, title=PRIMARY_TITLE, conclusion="success", event="workflow_dispatch",
        status="completed", branch="main"):
    finished = NOW - timedelta(minutes=minutes_ago)
    return {
        "display_title": title,
        "conclusion": conclusion,
        "event": event,
        "status": status,
        "head_branch": branch,
        "updated_at": finished.isoformat().replace("+00:00", "Z"),
    }


class BackupDecisionTests(unittest.TestCase):
    def test_recent_success_external_prevents_backup(self):
        self.assertTrue(has_recent_primary_success([run()], now=NOW))
        with patch("scripts.check_backup.get_workflow_runs", return_value=[run()]):
            self.assertFalse(backup_needed("guimig/Telegram-criseclimatica", "token", now=NOW))

    def test_old_external_success_requires_backup(self):
        self.assertFalse(has_recent_primary_success([run(minutes_ago=18)], now=NOW))

    def test_failed_external_or_manual_does_not_disable_backup(self):
        for candidate in (
            run(conclusion="failure"),
            run(status="in_progress", conclusion=None),
            run(title="Telegram risco | manual"),
            run(event="schedule"),
            run(branch="other"),
        ):
            with self.subTest(candidate=candidate):
                self.assertFalse(has_recent_primary_success([candidate], now=NOW))

    def test_rejects_missing_or_invalid_completion_time(self):
        self.assertFalse(has_recent_primary_success([{**run(), "updated_at": None}], now=NOW))
        self.assertFalse(has_recent_primary_success([{**run(), "updated_at": "broken"}], now=NOW))
        self.assertIsNone(parse_github_timestamp("not a time"))

    def test_failure_to_check_api_fails_open_to_backup(self):
        with patch("scripts.check_backup.get_workflow_runs", side_effect=OSError("offline")):
            self.assertTrue(backup_needed("guimig/Telegram-criseclimatica", "token", now=NOW))

    def test_recent_manual_test_does_not_mask_cron_job_failure(self):
        with patch("scripts.check_backup.get_workflow_runs",
                   return_value=[run(title="Telegram risco | manual"),
                                 run(minutes_ago=20)]):
            self.assertTrue(backup_needed("guimig/Telegram-criseclimatica", "token", now=NOW))

    def test_threshold_boundaries(self):
        self.assertTrue(has_recent_primary_success([run(minutes_ago=15)], now=NOW))
        self.assertFalse(has_recent_primary_success([run(minutes_ago=16)], now=NOW))
        self.assertFalse(has_recent_primary_success([run(minutes_ago=-10)], now=NOW))


if __name__ == "__main__":
    unittest.main()
