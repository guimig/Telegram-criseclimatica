"""Independent INMET danger notices for municipalities bordering Araquari.

The bot reads published official-source context; it never infers an Araquari
warning or modifies the IFC consultative risk level. First eligible publication
is a silent baseline; later new notices and severity escalations can notify.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from .source import SourceError, timestamp
from .message_formatter import PANEL, FOOTER, clip, local_time

KIND = "inmet_bordering_municipality_warning_context_not_araquari_alert"
IBGE = frozenset({"4202057", "4202107", "4206504", "4209102", "4210605", "4216206", "4216354"})
LOCAL = ZoneInfo("America/Sao_Paulo")
KEEP_DAYS = 45
LIMIT = 200


@dataclass(frozen=True)
class BorderingEvent:
    key: str
    rank: int
    title: str
    severity: str
    municipalities: tuple[str, ...]
    start_at: str
    end_at: str
    retained_previous: bool


def events_from_dashboard(dashboard: dict, at: str) -> list[BorderingEvent] | None:
    context = dashboard.get("bordering_notice_context")
    if context is None:
        return None  # Old site publication: do not start the migration cursor.
    if (not isinstance(context, dict) or context.get("schema_version") != 1
            or context.get("kind") != KIND or context.get("effect_on_local_risk") != "none"
            or context.get("checked_at") != at or context.get("status") not in
            ("available", "retained_unconfirmed", "unavailable")
            or not isinstance(context.get("notices"), list) or len(context["notices"]) > 100):
        raise SourceError("Contexto dos municípios limítrofes inválido")
    if context["status"] == "unavailable":
        return None  # Do not baseline/notify from an unverified initial collection.
    now = timestamp(at)
    selected = {}
    for notice in context["notices"]:
        if not isinstance(notice, dict):
            raise SourceError("Aviso regional inválido")
        try:
            start, end = timestamp(notice["start_at"]), timestamp(notice["end_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("Período regional inválido") from exc
        if not start < end or now >= end or start > now:
            continue  # Scheduled or expired warning, never message as ongoing.
        severity = notice.get("severity")
        rank = {"Perigo": 2, "Grande Perigo": 3}.get(severity)
        if rank is None:
            continue  # Potential danger is for the dashboard, not Telegram.
        if notice.get("scope") != "bordering_municipalities_only" or notice.get("araquari_included") is not False:
            raise SourceError("Aviso de Araquari não pode ser comunicado como aviso limítrofe")
        identifier = notice.get("id")
        municipalities = notice.get("municipalities")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 50
                or not isinstance(municipalities, list) or not 1 <= len(municipalities) <= len(IBGE)):
            raise SourceError("Identificador/municípios do aviso regional inválidos")
        names = []
        for place in municipalities:
            if (not isinstance(place, dict) or str(place.get("ibge_code")) not in IBGE
                    or not isinstance(place.get("name"), str) or not 1 <= len(place["name"]) <= 100):
                raise SourceError("Município de aviso limítrofe incompatível")
            names.append(place["name"].strip())
        day = start.astimezone(LOCAL).date().isoformat()
        raw = json.dumps([identifier, day], ensure_ascii=False, separators=(",", ":"))
        key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]
        event = BorderingEvent(
            key, rank, clip(notice.get("title") or "Aviso meteorológico", 120),
            severity, tuple(sorted(set(names))), notice["start_at"], notice["end_at"],
            bool(notice.get("retained_previous")) or context["status"] == "retained_unconfirmed",
        )
        if key not in selected or rank > selected[key].rank:
            selected[key] = event
    return sorted(selected.values(), key=lambda e: (-e.rank, e.start_at, e.key))


def plan_bordering(state: dict, events: list[BorderingEvent], at: str) -> tuple[dict, list[BorderingEvent]]:
    from .state import validate_state
    result = validate_state(state)
    now = timestamp(at)
    cutoff = now - timedelta(days=KEEP_DAYS)
    announced = {k: r for k, r in result["bordering_announcements"].items()
                 if timestamp(r["notified_at"]) >= cutoff}
    if not result["bordering_initialized"]:
        for event in events:
            announced[event.key] = {"rank": event.rank, "notified_at": at}
        result.update(bordering_initialized=True, bordering_announcements=announced)
        return validate_state(result), []
    pending = [event for event in events
               if not event.retained_previous
               and event.rank > announced.get(event.key, {}).get("rank", 0)]
    result["bordering_announcements"] = announced
    return validate_state(result), pending[:20]


def acknowledge_bordering(state: dict, event: BorderingEvent, at: str) -> dict:
    from .state import validate_state
    result = validate_state(state)
    result["bordering_announcements"][event.key] = {"rank": event.rank, "notified_at": at}
    if len(result["bordering_announcements"]) > LIMIT:
        # Keep the latest audit entries; no silent overwrite of the send result.
        older = sorted(result["bordering_announcements"], key=lambda k: timestamp(result["bordering_announcements"][k]["notified_at"]))
        for k in older[:len(older) - LIMIT]:
            del result["bordering_announcements"][k]
    result["bordering_initialized"] = True
    return validate_state(result)


def format_bordering(event: BorderingEvent, at: str, local_level: str | None) -> str:
    from .message_formatter import LABELS
    icon = "🔴" if event.rank == 3 else "🟠"
    names = ", ".join(event.municipalities)
    local = LABELS.get(local_level or "partial", LABELS["partial"])[1]
    lines = [
        f"{icon} AVISO INMET — {event.severity.upper()} NO ENTORNO DE ARAQUARI",
        "",
        f"{event.title} — {clip(names, 250)}.",
        f"Vigência oficial: {local_time(event.start_at)} até {local_time(event.end_at)}.",
        "",
        "O aviso foi emitido para município(s) limítrofe(s); não há confirmação de inclusão de Araquari neste aviso.",
        f"Nível consultivo de Araquari: {local} (não alterado por este aviso regional).",
        f"Consulta: {local_time(at)}.",
        "",
        "Acompanhe as condições locais e as atualizações dos órgãos oficiais.",
    ]
    if event.retained_previous:
        lines.append("Aviso preservado de consulta anterior; revisão ou cancelamento não reconfirmados.")
    return "\n".join(lines).strip()+"\n\n"+FOOTER
