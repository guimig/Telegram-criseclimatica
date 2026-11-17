"""Regressão do monitor e dos cenários materiais, sem rede e sem envios reais."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.evidence import Driver, extract_drivers, specific_notice_url
from src.main import process
from src.message_formatter import FOOTER, PANEL, format_continuity, format_transition
from src.situations import aggregate_situations
from src.source import DASHBOARD_KIND, HISTORY_KIND, RISK_KIND, Publication
from src.state import DEFAULT_STATE, read_state, write_state
from src.telegram_client import TelegramError

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
LOCAL = ZoneInfo("America/Sao_Paulo")


def iso(minutes: int = 0) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def local(day: str, hhmm: str) -> str:
    moment = datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=LOCAL)
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def notice(day: str = "2026-11-18", number: str = "28717",
           severity: str = "Perigo Potencial", scope: str = "municipality_explicit",
           source: str = "INMET", title: str = "Tempestade") -> dict:
    return {
        "source": source, "id": number, "title": title, "severity": severity,
        "url": "https://alertas2.inmet.gov.br/" if source == "INMET"
               else "https://ciram.epagri.sc.gov.br/index.php/2026/11/17/temporais-em-sc/",
        "scope": scope, "start_at": local(day, "00:00"),
        "end_at": local(day, "23:59"), "valid_until": local(day, "23:59"),
    }


def forecast(day: str = "2026-11-18", phenomena=("trovoadas",), source="INMET") -> dict:
    return {"source": source, "day": day, "period": "tarde",
            "phenomena": list(phenomena), "start_at": local(day, "12:00"),
            "end_at": local(day, "18:00"), "valid_until": iso(120)}


def build(*, notices=(), forecasts=(), level="amarelo", at=0,
          historic=None, signals=()) -> Publication:
    candidates = [
        {"level": "amarelo", "basis": "applicable_official_notice",
         "reason": f"{row['source']}: {row['title']}.",
         "valid_until": row["valid_until"], "evidence": [row]}
        for row in notices
    ]
    candidates.extend({
        "level": "amarelo", "basis": "inmet_qualitative_forecast_attention",
        "reason": f"{row['source']}: previsão de trovoadas.", "valid_until": iso(at + 120),
        "evidence": [row],
    } for row in forecasts)
    candidates.extend({
        "level": "amarelo", "basis": "numeric_attention",
        "reason": f"Rajadas: {row['value']} km/h previstas.",
        "valid_until": iso(at + 120), "evidence": [row],
    } for row in signals)
    moment = iso(at)
    risk = {"kind": RISK_KIND, "method_version": "araquari-risk-v4",
            "level": level, "official_level": None, "generated_at": moment,
            "candidates": candidates, "reason": candidates[0]["reason"] if candidates else "Sem sinais."}
    episodes = historic if historic is not None else [
        {"state": level or "partial", "first_seen_at": iso(-180),
         "last_seen_at": moment, "rule_version": "araquari-risk-v4"}
    ]
    dashboard = {"kind": DASHBOARD_KIND, "location_id": "araquari",
                 "generated_at": moment, "publication_at": moment,
                 "decision_automated": False, "risk_classification": risk}
    history = {"kind": HISTORY_KIND, "location_id": "araquari",
               "generated_at": moment, "publication_at": moment,
               "episodes": episodes}
    return Publication(dashboard, history, moment)


def old_state(level="amarelo") -> dict:
    return {**DEFAULT_STATE, "last_episode_at": iso(-180), "last_level": level}


def situations(publication: Publication) -> list:
    return aggregate_situations(extract_drivers(
        publication.dashboard["risk_classification"], at=publication.publication_at))


class PresentationTests(unittest.TestCase):
    def test_one_site_link_without_any_rss_or_article_link(self):
        warning = notice()
        risk = build(notices=[warning]).dashboard["risk_classification"]
        drivers = extract_drivers(risk, at=iso())
        # Links internos seguem disponíveis à auditoria, mas nunca no Telegram.
        self.assertEqual(drivers[0].url, "https://apiprevmet3.inmet.gov.br/avisos/rss/28717")
        message = format_continuity("amarelo", iso(), aggregate_situations(drivers))
        self.assertEqual(message.count("https://"), 1)
        self.assertIn(PANEL, message)
        self.assertNotIn("/avisos/rss/", message)
        self.assertNotIn("alertas2.inmet", message)
        self.assertTrue(message.endswith(FOOTER))

    def test_regional_epagri_without_municipality_not_announced(self):
        regional = notice("2026-11-17", "epagri-url", source="Epagri/Ciram",
                          title="Terça-feira com temporais em SC",
                          scope="regional_reference_not_municipal_confirmation")
        publication = build(notices=[regional])
        self.assertEqual(situations(publication), [])

    def test_inmet_pending_window_remains_distinct_from_confirmed_validity(self):
        publication = build()
        publication.dashboard["risk_classification"]["candidates"] = [{
            "level": "amarelo", "basis": "positive_notice_pending_time_verification",
            "reason": "INMET: Tempestade; validade a confirmar.",
            "valid_until": iso(120),
            "evidence": [{"id": "28717", "type": "Tempestade",
                          "url": "https://alertas2.inmet.gov.br/",
                          "window_as_reported": "18/11/2026 00:00 — 18/11/2026 23:59"}],
        }]
        events = situations(publication)
        self.assertEqual(len(events), 1)
        self.assertIn("Período oficial ainda não confirmado", events[0].summary)
        self.assertEqual(format_continuity("amarelo", iso(), events).count("https://"), 1)

    def test_epagri_url_remains_available_in_source_object(self):
        row = notice(source="Epagri/Ciram")
        self.assertEqual(specific_notice_url(row, "Epagri/Ciram"), row["url"])

    def test_duplicate_inmet_ids_same_day_are_one_situation(self):
        a = situations(build(notices=[notice(number="28717")]))
        b = situations(build(notices=[notice(number="28717"),
                                       notice(number="28721")]))
        self.assertEqual(len(a), len(b))
        self.assertEqual(a[0].key, b[0].key)
        self.assertEqual(a[0].rank, b[0].rank)

    def test_forecast_same_day_as_official_notice_does_not_change_material_key(self):
        a = situations(build(notices=[notice()]))
        b = situations(build(notices=[notice()], forecasts=[forecast()]))
        self.assertEqual([x.key for x in a], [x.key for x in b])
        self.assertEqual(a[0].rank, b[0].rank)

    def test_another_day_creates_new_material_key(self):
        a = situations(build(notices=[notice()]))
        b = situations(build(notices=[notice(), notice("2026-11-19", "28722")]))
        self.assertEqual(len(b), 2)
        self.assertNotEqual(a[0].key, b[-1].key)

    def test_severity_escalation_in_same_day_has_same_key_but_higher_rank(self):
        a = situations(build(notices=[notice(severity="Perigo Potencial")]))[0]
        b = situations(build(notices=[notice(severity="Perigo")]))[0]
        self.assertEqual(a.key, b.key)
        self.assertGreater(b.rank, a.rank)

    def test_severe_point_measurement_not_delayed(self):
        sample = Driver(
            key="synthetic", kind="sinal", source="IFC Araquari",
            reason="Rajadas elevadas observadas.", title="", url=None,
            start_at=local("2026-11-17", "10:00"),
            end_at=local("2026-11-17", "10:00"),
            variable="gust_kmh", nature="observation", band=3,
        )
        situations = aggregate_situations([sample])
        self.assertEqual(len(situations), 1)
        self.assertEqual(situations[0].rank, 30)

    def test_new_independent_hazard_in_same_day_gets_another_key(self):
        storm = situations(build(notices=[notice()]))
        new = situations(build(notices=[
            notice(), notice("2026-11-18", "28729", title="Ventos fortes")
        ]))
        self.assertEqual(len(storm), 1)
        self.assertEqual(len(new), 2)

    def test_change_level_message_is_immediate_clear_and_consultative(self):
        data = build(notices=[notice()])
        message = format_transition("verde", "amarelo", iso(), data.dashboard["risk_classification"])
        self.assertIn("Normalidade", message)
        self.assertIn("Atenção", message)
        self.assertIn("tempestades", message)
        self.assertEqual(message.count("https://"), 1)
        self.assertTrue(message.endswith(FOOTER))

    def test_long_event_summary_preserves_footer(self):
        data = build(notices=[
            notice(f"2026-11-{day:02}", number=f"287{day}")
            for day in range(18, 27)
        ])
        message = format_continuity("amarelo", iso(), situations(data))
        self.assertLessEqual(len(message), 3900)
        self.assertEqual(message.count("https://"), 1)
        self.assertTrue(message.endswith(FOOTER))


class OperationTests(unittest.TestCase):
    def run_monitor(self, path: Path, publication: Publication):
        with patch("src.main.load_publication", return_value=publication), \
             patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100100"}), \
             patch("src.main.send_telegram", return_value=57) as sender, \
             redirect_stdout(io.StringIO()):
            count = process(path)
        return count, [call.args[2] for call in sender.call_args_list]

    def test_migration_seeds_existing_events_without_sending(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            count, messages = self.run_monitor(path, build(notices=[notice()], forecasts=[forecast()]))
            self.assertEqual((count, messages), (0, []))
            current = read_state(path)
            self.assertTrue(current["material_initialized"])
            self.assertTrue(current["announced_situations"])
            self.assertEqual(current["last_level"], "amarelo")

    def test_new_day_pending_thirty_minutes_then_one_message_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice()]))
            new = [notice(), notice("2026-11-19", "28722")]
            count, msgs = self.run_monitor(path, build(notices=new, at=10))
            self.assertEqual((count, msgs), (0, []))
            self.assertEqual(len(read_state(path)["pending_situations"]), 1)
            count, msgs = self.run_monitor(path, build(notices=new, at=29))
            self.assertEqual(count, 0)
            count, msgs = self.run_monitor(path, build(notices=new, at=40))
            self.assertEqual((count, len(msgs)), (1, 1))
            self.assertIn("19/11/2026", msgs[0])
            self.assertNotIn("18/11/2026", msgs[0])
            self.assertEqual(msgs[0].count("https://"), 1)
            count, msgs = self.run_monitor(path, build(notices=new, at=50))
            self.assertEqual((count, msgs), (0, []))

    def test_consolidates_two_new_days_into_one_message(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice()]))
            self.run_monitor(path, build(notices=[
                notice(), notice("2026-11-19", "28722")
            ], at=10))
            count, msgs = self.run_monitor(path, build(notices=[
                notice(), notice("2026-11-19", "28722"), notice("2026-11-20", "28723")
            ], at=41))
            self.assertEqual((count, len(msgs)), (1, 1))
            self.assertIn("19/11/2026", msgs[0])
            self.assertIn("20/11/2026", msgs[0])

    def test_forecast_for_day_already_covered_by_notice_is_not_resent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice()]))
            count, msgs = self.run_monitor(path, build(
                notices=[notice(), notice(number="28721")],
                forecasts=[forecast()], at=50
            ))
            self.assertEqual((count, msgs), (0, []))

    def test_regional_publication_alone_no_continuity_notification(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice()]))
            regional = notice(
                "2026-11-17", "epagri17",
                scope="regional_reference_not_municipal_confirmation",
                source="Epagri/Ciram", title="Terça-feira com temporais em SC")
            count, msgs = self.run_monitor(path, build(notices=[notice(), regional], at=45))
            self.assertEqual((count, msgs), (0, []))

    def test_new_forecast_day_without_existing_notice_sends_after_delay(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(forecasts=[forecast()]))
            self.run_monitor(path, build(
                forecasts=[forecast(), forecast("2026-11-19")], at=5))
            count, msgs = self.run_monitor(path, build(
                forecasts=[forecast(), forecast("2026-11-19")], at=36))
            self.assertEqual((count, len(msgs)), (1, 1))
            self.assertIn("19/11/2026", msgs[0])

    def test_severity_upgrade_is_immediate_and_not_resent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice(severity="Perigo Potencial")]))
            count, msgs = self.run_monitor(path, build(
                notices=[notice(severity="Perigo")], at=10))
            self.assertEqual((count, len(msgs)), (1, 1))
            self.assertIn("(Perigo)", msgs[0])
            count, msgs = self.run_monitor(path, build(
                notices=[notice(severity="Perigo")], at=20))
            self.assertEqual((count, msgs), (0, []))

    def test_disappearing_pending_event_does_not_send_obsolete_notice(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice()]))
            self.run_monitor(path, build(notices=[notice(), notice("2026-11-19", "28722")], at=10))
            count, msgs = self.run_monitor(path, build(notices=[notice()], at=50))
            self.assertEqual((count, msgs), (0, []))
            self.assertFalse(read_state(path)["pending_situations"])

    def test_failed_send_keeps_pending_for_retry(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            self.run_monitor(path, build(notices=[notice()]))
            future = build(notices=[notice(), notice("2026-11-19", "28722")], at=5)
            self.run_monitor(path, future)
            pending = read_state(path)
            with patch("src.main.load_publication", return_value=build(
                    notices=[notice(), notice("2026-11-19", "28722")], at=36)), \
                 patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100100"}), \
                 patch("src.main.send_telegram", side_effect=TelegramError("network")), \
                 redirect_stdout(io.StringIO()):
                with self.assertRaises(TelegramError):
                    process(path)
            self.assertEqual(read_state(path)["announced_situations"], pending["announced_situations"])
            self.assertEqual(read_state(path)["pending_situations"], pending["pending_situations"])
            count, msgs = self.run_monitor(path, build(
                notices=[notice(), notice("2026-11-19", "28722")], at=42))
            self.assertEqual((count, len(msgs)), (1, 1))

    def test_transition_immediate_and_no_follow_up_for_same_event(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, {**old_state("verde"), "last_episode_at": iso(-300)})
            transition = build(notices=[notice()], historic=[
                {"state": "verde", "first_seen_at": iso(-300), "last_seen_at": iso(-190)},
                {"state": "amarelo", "first_seen_at": iso(-180), "last_seen_at": iso(0)},
            ])
            with patch("src.main.load_snapshots", return_value={}):
                count, msgs = self.run_monitor(path, transition)
            self.assertEqual((count, len(msgs)), (1, 1))
            self.assertIn("Normalidade", msgs[0])
            self.assertFalse(read_state(path)["pending_situations"])
            count, msgs = self.run_monitor(path, build(notices=[notice()], at=60))
            self.assertEqual((count, msgs), (0, []))

    def test_dry_run_does_not_write_any_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, old_state())
            before = path.read_bytes()
            with patch("src.main.load_publication", return_value=build(notices=[notice()])), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(process(path, dry_run=True), 0)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
