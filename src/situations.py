"""Situações meteorológicas materiais: consolida avisos, previsões e fontes.

São fatos que JUSTIFICAM o nível já calculado pelo Observatório, nunca outra
classificação. O grupo recebe uma síntese por data/fenômeno, não por URL/órgão.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .evidence import Driver
from .source import SourceError, timestamp

LOCAL = ZoneInfo("America/Sao_Paulo")
MAX_DAYS_PER_EVENT = 10
GROUP_LABELS = {
    "tempestade": "tempestades e trovoadas",
    "granizo": "granizo",
    "chuva_intensa": "chuva intensa",
    "vento_forte": "ventos fortes",
    "alagamento": "alagamentos",
    "outro": "condições meteorológicas adversas",
}


@dataclass(frozen=True)
class Situation:
    """Evento por data e fenômeno; rank representa materialidade, não nível IFC."""
    key: str
    day: str
    hazard: str
    rank: int
    summary: str
    source_names: tuple[str, ...]


def _hash(parts: list[str]) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def _dates(driver: Driver) -> list[str]:
    if driver.day:
        try:
            return [datetime.strptime(driver.day, "%Y-%m-%d").date().isoformat()]
        except ValueError:
            return []
    start = end = None
    if driver.start_at and driver.end_at:
        try:
            start = timestamp(driver.start_at).astimezone(LOCAL)
            end = timestamp(driver.end_at).astimezone(LOCAL)
        except SourceError:
            return []
        if end < start or (end == start and driver.kind != "sinal"):
            return []
        # Uma leitura pontual pode ter início e fim iguais; períodos de avisos
        # com fim exclusivo à meia-noite não criam um dia adicional.
        if end > start:
            end = end - timedelta(microseconds=1)
    elif driver.reported_window:
        # Período textual: informar apenas o dia sem afirmar vigência verificada.
        dates = re.findall(r"\b(\d{2}/\d{2}/\d{4})\b", driver.reported_window)
        if dates:
            try:
                start = datetime.strptime(dates[0], "%d/%m/%Y")
                end = datetime.strptime(dates[-1], "%d/%m/%Y")
            except ValueError:
                return []
    elif driver.end_at or driver.start_at:
        try:
            start = timestamp(driver.start_at or driver.end_at).astimezone(LOCAL)
            end = start
        except SourceError:
            return []
    if not start or not end:
        return []
    days = (end.date() - start.date()).days
    if days < 0 or days >= MAX_DAYS_PER_EVENT:
        return []
    return [(start.date() + timedelta(days=n)).isoformat() for n in range(days + 1)]


def _hazard(driver: Driver) -> str:
    text = " ".join([driver.title, driver.reason, " ".join(driver.phenomena)]).lower()
    if re.search(r"tempestade|temporais?|trovoadas?|raios?|trovoes|trovões", text):
        return "tempestade"
    if "granizo" in text:
        return "granizo"
    if re.search(r"alagamento|inundacao|inundação|enchente", text):
        return "alagamento"
    if re.search(r"vento|rajada", text):
        return "vento_forte"
    if re.search(r"chuva|precipitacao|precipitação", text):
        return "chuva_intensa"
    return "outro"


def _rank(driver: Driver) -> int:
    """Fonte municipal específica precede previsão; boletim apenas regional não dispara."""
    scope = (driver.scope or "").lower()
    source = driver.source.lower()
    if driver.kind == "aviso":
        if "regional" in scope and "municipal" in scope:
            return 0
        if "regional" in scope and "explicit" not in scope:
            return 0
        if "epagri" in source or "ciram" in source:
            # Epagri/Ciram é previsão meteorológica, não alerta da Defesa Civil.
            if "municipality_explicit" not in scope:
                return 0
        elif "inmet" not in source:
            return 0
        base = 20
        severity = (driver.severity or "").lower()
        if "grande perigo" in severity:
            return 40
        if "perigo" in severity and "potencial" not in severity:
            return 30
        return base
    if driver.kind == "previsão":
        return 10
    if driver.kind == "publicação":
        return 10 if "municipality_explicit" in scope else 0
    if driver.kind == "sinal":
        if driver.band is not None and driver.band >= 3:
            return 30
        return 15 if driver.band is not None and driver.band >= 2 else 0
    return 0


def _name(source: str) -> str:
    if "inmet" in source.lower():
        return "INMET"
    if "epagri" in source.lower() or "ciram" in source.lower():
        return "Epagri/Ciram"
    if "cptec" in source.lower():
        return "CPTEC/INPE"
    return source.strip() or "fonte monitorada"


def _describe(day: str, hazard: str, best: Driver, rank: int,
              all_drivers: list[Driver]) -> str:
    date = datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y")
    phenomenon = GROUP_LABELS[hazard]
    source = _name(best.source)
    if best.kind == "aviso" and source == "INMET":
        severity = (best.severity or "").strip()
        tail = f" ({severity})" if severity and severity.lower() != "não informada" else ""
        desc = f"{date}: {phenomenon} — aviso INMET para Araquari{tail}."
        if best.reported_window and not best.start_at:
            desc += " Período oficial ainda não confirmado."
    elif best.kind == "aviso":
        desc = f"{date}: {phenomenon} — publicação da {source} com abrangência municipal identificada."
    elif best.kind == "previsão":
        desc = f"{date}: previsão de {phenomenon} para Araquari ({source})."
    elif best.kind == "sinal":
        desc = f"{date}: novo sinal técnico de {phenomenon} nas referências monitoradas."
    else:
        desc = f"{date}: nova indicação de {phenomenon} para Araquari ({source})."
    if rank >= 20 and any(
        ("epagri" in x.source.lower() or "ciram" in x.source.lower())
        and "regional" in (x.scope or "").lower()
        for x in all_drivers
    ):
        desc += " A Epagri/Ciram também aponta condições regionais, sem confirmação municipal individual."
    return desc


def aggregate_situations(drivers: list[Driver]) -> list[Situation]:
    """Uma situação por dia/fenômeno; fontes repetidas não criam notificações."""
    grouped: dict[tuple[str, str], list[Driver]] = {}
    for driver in drivers:
        hazard = _hazard(driver)
        for day in _dates(driver):
            grouped.setdefault((day, hazard), []).append(driver)
    result: list[Situation] = []
    for (day, hazard), group in sorted(grouped.items()):
        best = max(group, key=lambda d: (_rank(d), d.kind == "aviso"))
        rank = _rank(best)
        if rank <= 0:
            # Boletim estadual/regional não garante aplicação a Araquari.
            continue
        key = _hash([day, hazard])
        result.append(Situation(
            key=key, day=day, hazard=hazard, rank=rank,
            summary=_describe(day, hazard, best, rank, group),
            source_names=tuple(sorted({_name(d.source) for d in group if d.source})),
        ))
    return sorted(result, key=lambda event: (event.day, event.hazard))
