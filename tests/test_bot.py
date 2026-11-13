"""Testes locais sintéticos sem rede e sem Telegram."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from src.message_formatter import format_transition
from src.source import DASHBOARD_KIND, HISTORY_KIND, RISK_KIND, SourceError, validate_publication
from src.state import DEFAULT_STATE, plan_steps, read_state, write_state
from src.main import process

NOW = datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc)
def stamp(offset=0):
    return (NOW + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
def episode(level, offset):
    return {"state": level, "first_seen_at": stamp(offset), "last_seen_at": stamp(offset)}
def publication(level="amarelo", episodes=None):
    from src.source import Publication
    rows = episodes if episodes is not None else [episode("verde", -50), episode("amarelo", -20)]
    dashboard = {"kind": DASHBOARD_KIND, "location_id": "araquari", "decision_automated": False,
                 "generated_at": stamp(), "publication_at": stamp(),
                 "risk_classification": {"kind": RISK_KIND, "level": level, "official_level": None,
                                         "method_version": "araquari-risk-v4", "reason": "INMET: tempestade.",
                                         "generated_at": stamp(), "candidates": []}}
    history = {"kind": HISTORY_KIND, "location_id": "araquari", "publication_at": stamp(),
               "generated_at": stamp(), "episodes": rows}
    return validate_publication(dashboard, history, now=NOW)

class MonitorTests(unittest.TestCase):
    def test_initial_baseline_without_notification(self):
        steps, baseline = plan_steps(DEFAULT_STATE, [episode("verde", -50), episode("amarelo", -20)])
        self.assertFalse(steps)
        self.assertEqual(baseline["last_level"], "amarelo")

    def test_real_transition_deduplication(self):
        state = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "verde"}
        episodes = [episode("verde", -50), episode("amarelo", -20)]
        steps, _ = plan_steps(state, episodes)
        self.assertTrue(steps[0]["notify"])
        next_steps, _ = plan_steps(steps[0]["next_state"], episodes)
        self.assertEqual(next_steps, [])

    def test_same_level_new_episode_only_advances_cursor(self):
        state = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "verde"}
        steps, _ = plan_steps(state, [episode("verde", -50), episode("verde", -20)])
        self.assertFalse(steps[0]["notify"])

    def test_missing_cursor_blocks_reinitialization(self):
        state = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "verde"}
        with self.assertRaises(SourceError):
            plan_steps(state, [episode("amarelo", -20)])

    def test_cursor_level_inconsistency_is_rejected(self):
        state = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "amarelo"}
        with self.assertRaises(SourceError):
            plan_steps(state, [episode("verde", -50), episode("amarelo", -20)])

    def test_state_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with self.assertRaises(FileNotFoundError):
                read_state(path)
            write_state(path, DEFAULT_STATE)
            self.assertEqual(read_state(path), DEFAULT_STATE)

    def test_validates_publication(self):
        self.assertEqual(publication().dashboard["risk_classification"]["level"], "amarelo")

    def test_refuses_mixed_and_stale_publication(self):
        data = publication()
        with self.assertRaises(SourceError):
            validate_publication(data.dashboard, dict(data.history, publication_at=stamp(-20)), now=NOW)
        with self.assertRaises(SourceError):
            validate_publication(data.dashboard, data.history, now=NOW + timedelta(hours=3))

    def test_refuses_history_mismatch(self):
        data = publication()
        data.dashboard["risk_classification"]["level"] = "verde"
        with self.assertRaises(SourceError):
            validate_publication(data.dashboard, data.history, now=NOW)

    def test_partial_level_not_normality(self):
        text = format_transition("verde", "partial", stamp(-20), None)
        self.assertIn("Sem classificação", text)
        self.assertIn("não equivale à normalidade", text)
        self.assertIn("não é alerta oficial", text)

    def test_failed_send_preserves_cursor(self):
        old = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "verde"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_state(path, old)
            with patch("src.main.load_publication", return_value=publication()), patch("src.main.load_snapshots", return_value={}), patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100100"}), patch("src.main.send_telegram", side_effect=RuntimeError("network")):
                with self.assertRaises(RuntimeError):
                    process(path)
            self.assertEqual(read_state(path), old)

    def test_success_and_repetition(self):
        old = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "verde"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            write_state(path, old)
            with patch("src.main.load_publication", return_value=publication()), patch("src.main.load_snapshots", return_value={}), patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100100"}), patch("src.main.send_telegram", return_value=42) as send:
                self.assertEqual(process(path), 1)
                self.assertEqual(process(path), 0)
                self.assertEqual(send.call_count, 1)

if __name__ == "__main__":
    unittest.main()
