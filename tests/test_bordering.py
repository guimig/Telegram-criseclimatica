"""Pure regional-notice processing and end-to-end Telegram state transitions."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src.bordering import (KIND, acknowledge_bordering, events_from_dashboard,
                           format_bordering, plan_bordering)
from src.main import process
from src.source import SourceError
from src.state import DEFAULT_STATE, read_state, write_state
from test_bot import publication, stamp, NOW


def context(*, danger="Perigo", identifier="1001", status="available",
            start=None, end=None, municipalities=None):
    return {"schema_version": 1, "kind": KIND, "effect_on_local_risk": "none",
            "checked_at": stamp(), "status": status, "notices": [{
                "id": identifier, "title": "Tempestade", "severity": danger,
                "start_at": start or stamp(-30), "end_at": end or stamp(90),
                "scope": "bordering_municipalities_only", "araquari_included": False,
                "retained_previous": False,
                "municipalities": municipalities if municipalities is not None else
                [{"id": "joinville", "name": "Joinville", "ibge_code": "4209102"}]}]}


class BorderEventTests(unittest.TestCase):
    def test_active_danger_and_severity(self):
        data = {"bordering_notice_context": context()}
        events = events_from_dashboard(data, stamp())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].rank, 2)
        self.assertEqual(events[0].municipalities, ("Joinville",))
        self.assertIn("Araquari", format_bordering(events[0], stamp(), "verde"))
        self.assertIn("não há confirmação de inclusão de Araquari", format_bordering(events[0], stamp(), "verde"))
        self.assertIn("Normalidade", format_bordering(events[0], stamp(), "verde"))
        self.assertEqual(format_bordering(events[0], stamp(), "verde").count("https://"), 1)

    def test_potential_and_scheduled_do_not_notify(self):
        self.assertEqual(events_from_dashboard({"bordering_notice_context": context(danger="Perigo Potencial")}, stamp()), [])
        self.assertEqual(events_from_dashboard({"bordering_notice_context": context(start=stamp(30))}, stamp()), [])
        self.assertEqual(events_from_dashboard({"bordering_notice_context": context(end=stamp(-1))}, stamp()), [])

    def test_unknown_context_does_not_initialize_state(self):
        self.assertIsNone(events_from_dashboard({}, stamp()))
        self.assertIsNone(events_from_dashboard({"bordering_notice_context": context(status="unavailable")}, stamp()))

    def test_fake_local_or_nonadjacent_rejected(self):
        for entries in ([{"name": "Araquari", "ibge_code": "4201307"}],
                        [{"name": "Itapoá", "ibge_code": "4208450"}]):
            with self.subTest(entries=entries), self.assertRaises(SourceError):
                events_from_dashboard({"bordering_notice_context": context(municipalities=entries)}, stamp())

    def test_baseline_repeat_and_upgrade(self):
        events = events_from_dashboard({"bordering_notice_context": context()}, stamp())
        state, ready = plan_bordering(DEFAULT_STATE, events, stamp())
        self.assertEqual(ready, [])
        self.assertTrue(state["bordering_initialized"])
        state, ready = plan_bordering(state, events, stamp())
        self.assertEqual(ready, [])
        stronger = events_from_dashboard({"bordering_notice_context": context(danger="Grande Perigo")}, stamp())
        state, ready = plan_bordering(state, stronger, stamp())
        self.assertEqual(len(ready), 1)
        state = acknowledge_bordering(state, ready[0], stamp())
        self.assertEqual(plan_bordering(state, stronger, stamp())[1], [])

    def test_duplicate_reference_same_id_not_repeated(self):
        item = context()["notices"][0]
        data = context()
        data["notices"].append({**item, "municipalities": [{"id": "guaramirim", "name": "Guaramirim", "ibge_code": "4206504"}]})
        self.assertEqual(len(events_from_dashboard({"bordering_notice_context": data}, stamp())), 1)

    def test_strict_published_version_check(self):
        invalid = context()
        invalid["checked_at"] = stamp(-10)
        with self.assertRaises(SourceError):
            events_from_dashboard({"bordering_notice_context": invalid}, stamp())

    def test_recovery_previous_not_new_notification(self):
        events = events_from_dashboard({"bordering_notice_context": context()}, stamp())
        state, _ = plan_bordering(DEFAULT_STATE, [], stamp())
        retained = [events[0].__class__(**{**vars(events[0]), "retained_previous": True})]
        self.assertEqual(plan_bordering(state, retained, stamp())[1], [])


class BorderIntegrationTests(unittest.TestCase):
    def run_process(self, path, pub):
        with patch("src.main.load_publication", return_value=pub), \
             patch("src.main.send_telegram", return_value=321) as sender, \
             patch.dict("os.environ", {"TELEGRAM_TOKEN": "fake", "TELEGRAM_CHAT_ID": "-10054321"}), \
             redirect_stdout(io.StringIO()):
            count = process(path)
        return count, [item.args[2] for item in sender.call_args_list]

    def test_migration_seeds_but_new_notice_messages_once(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            original = {**DEFAULT_STATE, "material_initialized": True,
                        "last_episode_at": stamp(-20), "last_level": "amarelo"}
            write_state(path, original)
            pub = publication()
            pub.dashboard["bordering_notice_context"] = context()
            self.assertEqual(self.run_process(path, pub)[0], 0)
            self.assertTrue(read_state(path)["bordering_initialized"])
            repeated = publication()
            repeated.dashboard["bordering_notice_context"] = context(identifier="1002")
            count, messages = self.run_process(path, repeated)
            self.assertEqual((count, len(messages)), (1, 1))
            self.assertIn("ENTORNO", messages[0])
            self.assertNotIn("→", messages[0])
            self.assertEqual(self.run_process(path, repeated)[0], 0)

    def test_failed_telegram_preserves_pending_notification(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, {**DEFAULT_STATE, "material_initialized": True,
                               "last_episode_at": stamp(-20), "last_level": "amarelo"})
            pub = publication()
            pub.dashboard["bordering_notice_context"] = context()
            self.run_process(path, pub)
            other = publication()
            other.dashboard["bordering_notice_context"] = context(identifier="2002")
            with patch("src.main.load_publication", return_value=other), \
                 patch("src.main.send_telegram", side_effect=RuntimeError("offline")), \
                 patch.dict("os.environ", {"TELEGRAM_TOKEN":"test","TELEGRAM_CHAT_ID":"-10054321"}), \
                 redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                process(path)
            self.assertEqual(len(read_state(path)["bordering_announcements"]), 1)
            self.assertEqual(self.run_process(path, other)[0], 1)


if __name__ == "__main__":
    unittest.main()
