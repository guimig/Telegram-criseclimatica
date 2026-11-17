"""Regressão: continuidade fundamentada, links individuais e idempotência."""
from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.evidence import extract_drivers, specific_notice_url
from src.main import process
from src.message_formatter import FOOTER, format_continuity, format_transition
from src.source import DASHBOARD_KIND, HISTORY_KIND, RISK_KIND, Publication
from src.state import DEFAULT_STATE, read_state, remember_evidence, write_state
from src.telegram_client import TelegramError

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)


def iso(offset: int) -> str:
    return (NOW + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")


def issue(number="28717", start=60, end=1440, source="INMET", title="Tempestade", severity="Perigo Potencial"):
    return {"source": source, "id": number, "title": title, "severity": severity,
            "url": "https://alertas2.inmet.gov.br/",
            "scope": "municipality_explicit", "start_at": iso(start),
            "end_at": iso(end), "valid_until": iso(end)}


def forecast(day="2026-11-17", phenomenon="trovoadas", period="noite"):
    return {"source": "INMET", "day": day, "period": period,
            "phenomena": [phenomenon], "start_at": iso(1020),
            "end_at": iso(1200), "valid_until": iso(180)}


def signal(value=40.0, end=900):
    return {"source": "Open-Meteo", "station": "araquari", "nature": "forecast",
            "variable": "gust_kmh", "value": value, "band": 1,
            "start_at": iso(end), "end_at": iso(end),
            "valid_until": iso(200)}


def build(level="amarelo", notices=(), forecasts=(), signals=(), additional=()):
    candidates = []
    for notice in notices:
        candidates.append({"level": "amarelo", "basis": "applicable_official_notice",
                           "reason": "INMET: Tempestade.", "valid_until": iso(1440),
                           "evidence": [notice]})
    for row in forecasts:
        candidates.append({"level": "amarelo", "basis": "inmet_qualitative_forecast_attention",
                           "reason": "INMET: previsão de trovoadas.", "valid_until": iso(180),
                           "evidence": [row]})
    for row in signals:
        candidates.append({"level": "amarelo", "basis": "numeric_attention",
                           "reason": f"Rajadas: {row['value']} km/h previstas.",
                           "valid_until": iso(200), "evidence": [row]})
    candidates += list(additional)
    risk = {"kind": RISK_KIND, "method_version": "araquari-risk-v4",
            "level": level, "official_level": None, "generated_at": iso(0),
            "candidates": candidates, "reason": candidates[0]["reason"] if candidates else "Sem sinais."}
    episodes = [{"state": level or "partial", "first_seen_at": iso(-180),
                 "last_seen_at": iso(0), "rule_version": "araquari-risk-v4"}]
    dashboard = {"kind": DASHBOARD_KIND, "location_id": "araquari",
                 "generated_at": iso(0), "publication_at": iso(0),
                 "decision_automated": False, "risk_classification": risk}
    history = {"kind": HISTORY_KIND, "location_id": "araquari",
               "generated_at": iso(0), "publication_at": iso(0),
               "episodes": episodes}
    return Publication(dashboard, history, iso(0))


def state_of(level="amarelo"):
    return {**DEFAULT_STATE, "last_episode_at": iso(-180),
            "last_level": level}


class EvidenceTests(unittest.TestCase):
    def test_individual_inmet_link_not_generic_homepage(self):
        risk = build(notices=[issue()]).dashboard["risk_classification"]
        drivers = extract_drivers(risk, at=iso(0))
        self.assertEqual(drivers[0].url, "https://avisos.inmet.gov.br/28717")
        message = format_continuity("amarelo", iso(0), drivers)
        self.assertIn("https://avisos.inmet.gov.br/28717", message)
        self.assertNotIn("https://alertas2.inmet.gov.br/", message)
        self.assertIn("18/11/2026", message)
        self.assertTrue(message.endswith(FOOTER))

    def test_epagri_individual_article_only(self):
        epagri = {"url": "https://ciram.epagri.sc.gov.br/index.php/2026/11/16/temporais-em-sc/",
                  "source": "Epagri/Ciram", "title": "Temporais"}
        self.assertEqual(specific_notice_url(epagri, "Epagri/Ciram"), epagri["url"])
        self.assertIsNone(specific_notice_url(
            {**epagri, "url": "https://ciram.epagri.sc.gov.br/"}, "Epagri/Ciram"))
        self.assertIsNone(specific_notice_url(
            {**epagri, "url": "https://outraempresa.com/temporais"}, "Epagri/Ciram"))

    def test_new_notice_same_level_gets_distinct_id(self):
        risk1 = build(notices=[issue("28717")]).dashboard["risk_classification"]
        risk2 = build(notices=[issue("28717"), issue("28718", start=1500, end=2800)]).dashboard["risk_classification"]
        first = {d.key for d in extract_drivers(risk1, at=iso(0))}
        later = {d.key for d in extract_drivers(risk2, at=iso(0))}
        self.assertEqual(len(later - first), 1)

    def test_revision_date_is_new_but_capture_time_is_not(self):
        row = issue()
        base = build(notices=[row]).dashboard["risk_classification"]
        original = extract_drivers(base, at=iso(0))[0].key
        same = {**row, "retrieved_at": iso(10), "last_seen_at": iso(10), "valid_until": iso(4000)}
        equal = build(notices=[same]).dashboard["risk_classification"]
        self.assertEqual(extract_drivers(equal, at=iso(0))[0].key, original)
        revised = build(notices=[{**row, "end_at": iso(2000)}]).dashboard["risk_classification"]
        self.assertNotEqual(extract_drivers(revised, at=iso(0))[0].key, original)

    def test_new_forecast_day_or_phenomenon_not_refresh(self):
        old = build(forecasts=[forecast()]).dashboard["risk_classification"]
        keys = {x.key for x in extract_drivers(old, at=iso(0))}
        refreshed = build(forecasts=[{**forecast(), "collected_at": iso(30)}]).dashboard["risk_classification"]
        self.assertEqual(keys, {x.key for x in extract_drivers(refreshed, at=iso(0))})
        next_day = build(forecasts=[forecast(), forecast(day="2026-11-18")]).dashboard["risk_classification"]
        self.assertEqual(len(set(x.key for x in extract_drivers(next_day, at=iso(0))) - keys), 1)
        thunder_hail = build(forecasts=[forecast(phenomenon="granizo")]).dashboard["risk_classification"]
        self.assertNotEqual(keys, {x.key for x in extract_drivers(thunder_hail, at=iso(0))})

    def test_numeric_changes_same_day_are_not_an_event(self):
        one = build(signals=[signal(40.0)]).dashboard["risk_classification"]
        two = build(signals=[signal(42.0, end=930)]).dashboard["risk_classification"]
        self.assertEqual(
            {d.key for d in extract_drivers(one, at=iso(0))},
            {d.key for d in extract_drivers(two, at=iso(0))})

    def test_lower_level_candidate_cannot_explain_higher_level(self):
        risk = build(notices=[issue()]).dashboard["risk_classification"]
        risk["level"] = "laranja"
        risk["candidates"].append({"level": "laranja", "basis": "scoped_dated_publication_attention",
                                    "reason": "Epagri: temporal severo.", "valid_until": iso(1000),
                                    "evidence": [{"source": "Epagri/Ciram", "id": "report42",
                                                  "phenomena": ["temporal"], "day": "2026-11-17"}]})
        drivers = extract_drivers(risk, at=iso(0))
        self.assertEqual(len(drivers), 1)
        self.assertNotEqual(drivers[0].kind, "aviso")

    def test_transition_shows_all_relevant_basis(self):
        risk = build(notices=[issue()], signals=[signal()], forecasts=[forecast()]).dashboard["risk_classification"]
        text = format_transition("verde", "amarelo", iso(0), risk)
        self.assertIn("https://avisos.inmet.gov.br/28717", text)
        self.assertIn("40.0 km/h", text)
        self.assertIn("trovoadas", text)
        self.assertTrue(text.endswith(FOOTER))

    def test_long_message_keeps_footer_and_valid_links(self):
        risk = build(notices=[issue(str(28717+n), start=60+n, end=1440+n)
                              for n in range(22)]).dashboard["risk_classification"]
        text = format_continuity("amarelo", iso(0), extract_drivers(risk, at=iso(0)))
        self.assertLessEqual(len(text), 3900)
        self.assertIn("https://avisos.inmet.gov.br/", text)
        self.assertTrue(text.endswith(FOOTER))

    def test_green_and_unclassified_never_generate_continuity(self):
        for level in ("verde", None):
            risk = build(level=level, notices=[issue()]).dashboard["risk_classification"]
            self.assertEqual(extract_drivers(risk, at=iso(0)), [])


class IntegrationTests(unittest.TestCase):
    def _run(self, file: Path, publication: Publication, *, send=None):
        with patch("src.main.load_publication", return_value=publication), \
                patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100123"}), \
                patch("src.main.send_telegram", return_value=123) as sender, \
                redirect_stdout(io.StringIO()):
            result = process(file)
        return result, sender.call_args_list

    def test_upgrade_seeds_evidence_silently_then_sends_new_notice_once(self):
        before = build(notices=[issue("28717")], forecasts=[forecast()])
        after = build(notices=[issue("28717"), issue("28718", start=1500, end=2800)],
                      forecasts=[forecast()])
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, state_of())
            count, messages = self._run(path, before)
            self.assertEqual((count, len(messages)), (0, 0))
            self.assertTrue(read_state(path)["evidence_initialized"])
            count, messages = self._run(path, after)
            self.assertEqual((count, len(messages)), (1, 1))
            self.assertIn("CONTINUIDADE", messages[0].args[2])
            self.assertIn("https://avisos.inmet.gov.br/28718", messages[0].args[2])
            self.assertNotIn("https://avisos.inmet.gov.br/28717", messages[0].args[2])
            count, messages = self._run(path, after)
            self.assertEqual((count, len(messages)), (0, 0))

    def test_returning_notice_is_not_resent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, state_of())
            self._run(path, build(notices=[issue()], signals=[signal()]))
            self._run(path, build(signals=[signal()]))
            count, msgs = self._run(path, build(notices=[issue()], signals=[signal()]))
            self.assertEqual((count, len(msgs)), (0, 0))

    def test_new_forecast_day_sends_continuation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, state_of())
            self._run(path, build(forecasts=[forecast()]))
            count, msgs = self._run(path, build(forecasts=[forecast(), forecast(day="2026-11-18")]))
            self.assertEqual((count, len(msgs)), (1, 1))
            self.assertIn("18/11/2026", msgs[0].args[2])

    def test_send_failure_preserves_old_signatures(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_state(path, state_of())
            self._run(path, build(notices=[issue()]))
            old_state = read_state(path)
            new_publication = build(notices=[issue(), issue("28718", start=1500, end=2800)])
            with patch("src.main.load_publication", return_value=new_publication), \
                    patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100123"}), \
                    patch("src.main.send_telegram", side_effect=TelegramError("network")):
                with self.assertRaises(TelegramError):
                    process(path)
            self.assertEqual(read_state(path), old_state)

    def test_transition_does_not_duplicate_as_continuity(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            current = build(notices=[issue()])
            current.history["episodes"][:] = [
                {"state": "verde", "first_seen_at": iso(-300), "last_seen_at": iso(-190)},
                {"state": "amarelo", "first_seen_at": iso(-180), "last_seen_at": iso(0)},
            ]
            write_state(path, {**state_of("verde"), "last_episode_at": iso(-300)})
            with patch("src.main.load_publication", return_value=current), \
                    patch("src.main.load_snapshots", return_value={}), \
                    patch.dict("os.environ", {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "-100123"}), \
                    patch("src.main.send_telegram", return_value=99) as sender, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(process(path), 1)
            self.assertEqual(sender.call_count, 1)
            self.assertEqual(read_state(path)["last_level"], "amarelo")


if __name__ == "__main__":
    unittest.main()
