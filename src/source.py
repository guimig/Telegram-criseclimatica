"""Leitura e validação dos dados PUBLICADOS pelo Observatório, sem recalcular riscos."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://guimig.github.io/criseclimatica"
STATES = frozenset({"verde", "azul", "amarelo", "laranja", "vermelho", "roxo", "partial"})
DASHBOARD_KIND = "araquari_weather_dashboard_not_official_decision"
HISTORY_KIND = "araquari_assessment_episodes_and_epagri_notice_history"
RISK_KIND = "consultative_risk_level_not_official_decision"
MULTISOURCE_KIND = "araquari_normalized_evidence_history"


class SourceError(RuntimeError):
    """Fonte indisponível, desatualizada ou incompatível com o contrato."""


@dataclass(frozen=True)
class Publication:
    dashboard: dict[str, Any]
    history: dict[str, Any]
    publication_at: str


def timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise SourceError("Horário obrigatório ausente nos dados publicados")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceError("Horário inválido nos dados publicados") from exc
    if parsed.tzinfo is None:
        raise SourceError("Horário sem fuso nos dados publicados")
    return parsed.astimezone(timezone.utc)


def get_json(base_url: str, file_name: str, *, max_bytes: int = 5_000_000) -> dict[str, Any]:
    """Recusa respostas truncadas, HTML e arquivos demasiadamente grandes."""
    if not base_url.startswith("https://"):
        raise SourceError("A origem do painel deve utilizar HTTPS")
    url = f"{base_url.rstrip('/')}/data/{file_name}?bot_check={time.time_ns()}"
    request = Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache",
                                    "User-Agent": "Telegram-criseclimatica/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        raise SourceError(f"HTTP {exc.code} ao obter {file_name}") from None
    except (URLError, TimeoutError, OSError):
        raise SourceError(f"Falha de rede ao obter {file_name}") from None
    if len(data) > max_bytes:
        raise SourceError(f"Arquivo {file_name} excede o limite de segurança")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceError(f"JSON inválido em {file_name}") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"Objeto JSON esperado em {file_name}")
    if payload.get("fixture_mode"):
        raise SourceError(f"Dados de teste recusados em {file_name}")
    return payload


def validate_publication(dashboard: dict, history: dict, *, now: datetime | None = None) -> Publication:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise SourceError("O relógio local precisa ter fuso")
    if (dashboard.get("kind") != DASHBOARD_KIND or dashboard.get("location_id") != "araquari"
            or dashboard.get("decision_automated") is not False
            or history.get("kind") != HISTORY_KIND or history.get("location_id") != "araquari"):
        raise SourceError("Contratos ou localidade incompatíveis com o Observatório")
    risk = dashboard.get("risk_classification")
    if (not isinstance(risk, dict) or risk.get("kind") != RISK_KIND
            or risk.get("official_level") is not None
            or risk.get("level") not in (None, *STATES - {"partial"})):
        raise SourceError("Classificação consultiva incompatível com o contrato")
    stamp = dashboard.get("publication_at")
    if (not isinstance(stamp, str) or stamp != history.get("publication_at")
            or stamp != dashboard.get("generated_at")
            or stamp != history.get("generated_at")
            or stamp != risk.get("generated_at")):
        raise SourceError("Arquivos de publicações diferentes (CDN/cache); aguardar consistência")
    age = now.astimezone(timezone.utc) - timestamp(stamp)
    if age < -timedelta(minutes=5) or age > timedelta(hours=2):
        raise SourceError("Publicação fora da janela de atualidade de duas horas")
    episodes = history.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise SourceError("Histórico de níveis ausente ou vazio")
    previous_at = None
    for episode in episodes:
        if not isinstance(episode, dict) or episode.get("state") not in STATES:
            raise SourceError("Episódio inválido no histórico de níveis")
        seen_at = timestamp(episode.get("first_seen_at"))
        if seen_at > timestamp(stamp) or (previous_at is not None and seen_at <= previous_at):
            raise SourceError("Episódios fora de ordem temporal")
        previous_at = seen_at
    if episodes[-1]["state"] != (risk["level"] or "partial"):
        raise SourceError("Nível atual diverge do último episódio publicado")
    if not risk.get("method_version"):
        raise SourceError("Versão do método consultivo não informada")
    return Publication(dashboard=dashboard, history=history, publication_at=stamp)


def load_publication(base_url: str = BASE_URL, *, retries: int = 4) -> Publication:
    for attempt in range(retries):
        try:
            dashboard = get_json(base_url, "dashboard.json")
            history = get_json(base_url, "assessment_history.json")
            return validate_publication(dashboard, history)
        except SourceError:
            if attempt == retries - 1:
                raise
            time.sleep(2)
    raise SourceError("Não foi possível obter uma publicação coerente")


def load_snapshots(base_url: str, publication_at: str) -> dict[str, dict[str, Any]]:
    """Detalhes dos episódios recentes, opcionais; jamais substituir a fonte de estado."""
    history = get_json(base_url, "multisource_history.json", max_bytes=18_000_000)
    if (history.get("kind") != MULTISOURCE_KIND or history.get("location_id") not in (None, "araquari")
            or history.get("publication_at") != publication_at):
        raise SourceError("Memória multifontes não corresponde à publicação consultada")
    snapshots: dict[str, dict[str, Any]] = {}
    for entry in history.get("assessments", []):
        if not isinstance(entry, dict):
            continue
        risk = entry.get("risk_classification")
        if not isinstance(risk, dict) or risk.get("kind") != RISK_KIND:
            continue
        if risk.get("official_level") is not None or risk.get("level") not in (None, *STATES - {"partial"}):
            continue
        if entry.get("generated_at") == risk.get("generated_at"):
            snapshots[entry["generated_at"]] = risk
    return snapshots
