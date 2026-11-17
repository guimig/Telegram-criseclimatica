"""Fundamentações materiais que justificam continuidade do nível consultivo.

O fingerprint não inclui relógio de coleta, TTL técnico ou valor numérico exato.
Somente eventos identificáveis, novos períodos de previsão e mudança de faixa
podem originar mensagens. O algoritmo do Observatório segue como fonte da verdade.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .source import STATES, SourceError, timestamp

LOCAL = ZoneInfo("America/Sao_Paulo")
POSITIVE_LEVELS = STATES - {"verde", "partial"}
NOTICE_BASES = frozenset({
    "applicable_official_notice",
    "qualitative_epagri_evidence",
    "positive_notice_pending_time_verification",
})
FORECAST_BASES = frozenset({
    "inmet_qualitative_forecast_attention",
    "cptec_qualitative_forecast_attention",
})
SIGNAL_BASES = frozenset({
    "numeric_attention",
    "approximate_local_observation",
    "observed_shorter_window_lower_bound_24h",
    "compatible_observation_and_forecast",
    "compatible_observation_and_additional_model",
})
PUBLICATION_BASES = frozenset({"scoped_dated_publication_attention"})


@dataclass(frozen=True)
class Driver:
    key: str
    kind: str
    source: str
    reason: str
    title: str
    url: str | None
    start_at: str | None = None
    end_at: str | None = None
    day: str | None = None
    period: str | None = None
    phenomena: tuple[str, ...] = ()
    severity: str | None = None
    scope: str | None = None
    reported_window: str | None = None
    variable: str | None = None
    nature: str | None = None
    band: int | None = None


def clean(value: object) -> str:
    return " ".join(str(value or "").split())


def day_of(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return timestamp(value).astimezone(LOCAL).date().isoformat()
    except SourceError:
        return None


def specific_notice_url(row: dict, source: str) -> str | None:
    """Não apresentar uma página inicial como se fosse o aviso individual."""
    raw = clean(row.get("url"))
    parsed = urlparse(raw)
    domain = (parsed.hostname or "").lower()
    if source.upper().startswith("INMET"):
        number = str(row.get("id") or "").strip()
        # O ID vem de id_aviso da API; o portal HTML usa outro identificador.
        # O item CAP/RSS é individual, evitando link para aviso HTML incorreto.
        if re.fullmatch(r"[0-9]{3,12}", number):
            if domain in ("alertas2.inmet.gov.br", "avisos.inmet.gov.br", "apiprevmet3.inmet.gov.br", ""):
                return f"https://apiprevmet3.inmet.gov.br/avisos/rss/{number}"
        if (parsed.scheme == "https" and domain in ("avisos.inmet.gov.br", "alertas2.inmet.gov.br")
                and re.fullmatch(r"/[0-9]{3,12}/?", parsed.path)):
            return raw
        return None
    if source.upper().startswith("EPAGRI") or "CIRAM" in source.upper():
        if parsed.scheme != "https" or not domain.endswith(".epagri.sc.gov.br"):
            return None
        if parsed.path.rstrip("/") in ("", "/index.php", "/category", "/avisos"):
            return None
        if parsed.path.startswith("/index.php/category/"):
            return None
        return raw
    return None


def _hash(parts: list[object]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:28]


def _relevant_evidence(candidate: dict) -> list[dict]:
    rows = candidate.get("evidence")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def extract_drivers(risk: dict, *, at: str) -> list[Driver]:
    """Extrai os fatos do nível atual, sem promover evidências inferiores a causa.

    Não são disparos: alteração de minuto, mudança de medição dentro da mesma faixa,
    vencimento técnico renovado, rotação do modelo no mesmo fenômeno/período.
    """
    if not isinstance(risk, dict):
        return []
    level = risk.get("level") or "partial"
    if level not in POSITIVE_LEVELS:
        return []
    current = timestamp(at)
    result: dict[str, Driver] = {}
    for candidate in risk.get("candidates") or []:
        if not isinstance(candidate, dict) or candidate.get("level") != level:
            continue
        try:
            if timestamp(candidate.get("valid_until")) <= current:
                continue
        except SourceError:
            continue
        basis = clean(candidate.get("basis"))
        reason = clean(candidate.get("reason"))
        for row in _relevant_evidence(candidate):
            source = clean(row.get("source") or row.get("name") or row.get("station") or "")
            if not source and basis == "positive_notice_pending_time_verification":
                source = "INMET"
            if not source and basis == "qualitative_epagri_evidence":
                source = "Epagri/Ciram"
            title = clean(row.get("title") or row.get("type") or "")
            start = clean(row.get("start_at")) or None
            end = clean(row.get("end_at")) or None
            severity = clean(row.get("severity")) or None
            day = clean(row.get("day")) or None
            period = clean(row.get("period")) or None
            phenomena = tuple(sorted({clean(p).lower() for p in row.get("phenomena", [])
                                      if isinstance(p, str) and clean(p)})) if isinstance(row.get("phenomena"), list) else ()
            kind = ""
            parts: list[object] = []
            if basis in NOTICE_BASES:
                kind = "aviso"
                ident = clean(row.get("id"))
                url = specific_notice_url(row, source)
                if not ident and not url and not title:
                    continue
                parts = [kind, source.upper(), ident or url or title.lower(), start, end, severity]
            elif basis in FORECAST_BASES:
                kind = "previsão"
                if not day or not period or not phenomena:
                    continue
                # Os turnos são operacionais, não horários emitidos como instante
                # exato pela fonte. A atualização da consulta não modifica o evento.
                parts = [kind, source.upper(), day, period, phenomena]
            elif basis in PUBLICATION_BASES:
                kind = "publicação"
                identity = clean(row.get("id") or row.get("url") or "")
                if not identity and not (day or start):
                    continue
                parts = [kind, source.upper(), identity, day or day_of(start), period,
                         phenomena or title.lower()]
            elif basis in SIGNAL_BASES:
                kind = "sinal"
                variable = clean(row.get("variable"))
                if not variable:
                    continue
                # Máximos das previsões mudam a cada execução; agrupar por dia
                # civil, fenômeno, natureza e faixa, nunca por amostra/valor.
                nature = clean(row.get("nature") or ("forecast" if "forecast" in basis else "observation"))
                band = row.get("band")
                if band is not None and not isinstance(band, int):
                    continue
                signal_day = day_of(end or start or row.get("at"))
                if not signal_day:
                    continue
                parts = [kind, basis, variable, nature, band, signal_day,
                         clean(row.get("scope") or "araquari")]
            else:
                continue
            key = _hash([level, *parts])
            result.setdefault(key, Driver(
                key=key, kind=kind, source=source, reason=reason,
                title=title, url=specific_notice_url(row, source) if kind == "aviso" else None,
                start_at=start, end_at=end, day=day, period=period,
                phenomena=phenomena, severity=severity,
                scope=clean(row.get("scope")) or None,
                reported_window=clean(row.get("window_as_reported")) or None,
                variable=clean(row.get("variable")) or None,
                nature=clean(row.get("nature")) or None,
                band=row.get("band") if type(row.get("band")) is int else None,
            ))
    # Priorizar avisos oficiais diante de previsões e sinais instrumentais.
    order = {"aviso": 0, "publicação": 1, "previsão": 2, "sinal": 3}
    return sorted(result.values(), key=lambda d: (order.get(d.kind, 9), d.source, d.key))
