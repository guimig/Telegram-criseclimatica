"""Testes locais sintéticos sem rede e sem Telegram."""
import tempfile
import io
import json
from contextlib import redirect_stdout
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from src.message_formatter import format_transition
from src.source import DASHBOARD_KIND, HISTORY_KIND, RISK_KIND, SourceError, validate_publication
from src.state import DEFAULT_STATE, plan_steps, read_state, write_state
from src.main import process
from src.telegram_client import send_telegram, TelegramError

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
    def test_telegram_rejects_private_id_before_network(self):
        with patch("src.telegram_client.urlopen") as opened:
            with self.assertRaises(TelegramError):
                send_telegram("token-falso", "123456", "Teste")
            opened.assert_not_called()

    def test_telegram_confirms_group_as_destination(self):
        payload = {"ok": True, "result": {"message_id": 128, "chat": {"id": -100123, "type": "supergroup"}}}
        with patch("src.telegram_client.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            self.assertEqual(send_telegram("token-falso", "-100123", "Teste"), 128)

    def test_telegram_refuses_wrong_group_in_response(self):
        payload = {"ok": True, "result": {"message_id": 128, "chat": {"id": -100999, "type": "supergroup"}}}
        with patch("src.telegram_client.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            with self.assertRaises(TelegramError):
                send_telegram("token-falso", "-100123", "Teste")

    def test_accepts_published_legacy_episodes_without_replaying_them(self):
        # O historico real de novembro/2026 preserva os estados antigos do PN13.
        episodes = [
            {**episode("technical_attention", -150), "rule_version": "pn13-14-2026-multisource-v3"},
            {**episode("routine", -110), "rule_version": "pn13-14-2026-multisource-v3"},
            {**episode("partial", -90), "rule_version": "pn13-14-2026-multisource-v3"},
            {**episode("verde", -50), "rule_version": "araquari-risk-v3"},
            {**episode("amarelo", -20), "rule_version": "araquari-risk-v4"},
        ]
        result = publication(episodes=episodes)
        steps, baseline = plan_steps(DEFAULT_STATE, result.history["episodes"])
        self.assertEqual(steps, [])
        self.assertEqual(baseline["last_level"], "amarelo")
        self.assertEqual(baseline["last_episode_at"], stamp(-20))

    def test_rejects_unknown_level_in_recent_classification(self):
        episodes = [
            {**episode("strange", -50), "rule_version": "araquari-risk-v4"},
            {**episode("amarelo", -20), "rule_version": "araquari-risk-v4"},
        ]
        with self.assertRaises(SourceError):
            publication(episodes=episodes)

    def test_legacy_state_after_operational_cursor_is_not_alerted(self):
        persisted = {**DEFAULT_STATE, "last_episode_at": stamp(-50), "last_level": "verde"}
        episodes = [
            episode("verde", -50),
            {**episode("routine", -20), "rule_version": "pn13-14-2026-multisource-v3"},
            episode("amarelo", -10),
        ]
        with self.assertRaises(SourceError):
            plan_steps(persisted, episodes)

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
                # Impedir que um envio simulado apareca como entrega real nos logs da CI.
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(process(path), 1)
                    self.assertEqual(process(path), 0)
                self.assertIn("[ENVIADO]", output.getvalue())
                self.assertEqual(send.call_count, 1)

if __name__ == "__main__":
    unittest.main()
