"""Progresso durável por episódio amostrado, independente do GitHub Actions cache."""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from .source import STATES, SourceError, timestamp

DEFAULT_STATE = {"schema_version": 1, "location_id": "araquari",
                 "last_episode_at": None, "last_level": None,
                 "evidence_initialized": False, "seen_evidence": {},
                 "material_initialized": False,
                 "announced_situations": {}, "pending_situations": {}}
SEEN_RETENTION_DAYS = 45
MAX_SEEN_EVIDENCE = 500
CONSOLIDATION_MINUTES = 30
MAX_MATERIAL_SITUATIONS = 500


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if (not isinstance(state, dict) or state.get("schema_version") != 1
            or state.get("location_id") != "araquari"):
        raise ValueError("Arquivo de estado incompatível")
    episode_at = state.get("last_episode_at")
    level = state.get("last_level")
    if (episode_at is None) != (level is None):
        raise ValueError("Estado parcial/inconsistente")
    if episode_at is not None:
        timestamp(episode_at)
        if level not in STATES:
            raise ValueError("Nível inválido no estado persistido")
    # Compatibilidade com o cursor legado, gravado antes das notificacoes
    # de continuidade: na primeira rodada apenas semear os eventos atuais.
    result = dict(DEFAULT_STATE)
    result.update({key: state[key] for key in ("schema_version", "location_id",
                                               "last_episode_at", "last_level")})
    initialized = state.get("evidence_initialized", False)
    seen = state.get("seen_evidence", {})
    if not isinstance(initialized, bool) or not isinstance(seen, dict) or len(seen) > MAX_SEEN_EVIDENCE:
        raise ValueError("Registro de fundamentacoes incompatível")
    for key, first_seen in seen.items():
        if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{28}", key):
            raise ValueError("Identificador de fundamentacao inválido")
        timestamp(first_seen)
    result["evidence_initialized"] = initialized
    result["seen_evidence"] = dict(seen)
    material_initialized = state.get("material_initialized", False)
    if not isinstance(material_initialized, bool):
        raise ValueError("Indicador de situações materiais inválido")
    result["material_initialized"] = material_initialized
    for field, time_field in (("announced_situations", "notified_at"),
                              ("pending_situations", "first_seen_at")):
        items = state.get(field, {})
        if not isinstance(items, dict) or len(items) > MAX_MATERIAL_SITUATIONS:
            raise ValueError("Índice de situações materiais inválido")
        verified = {}
        for key, record in items.items():
            if (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{28}", key)
                    or not isinstance(record, dict)):
                raise ValueError("Registro de situação inválido")
            rank = record.get("rank")
            if type(rank) is not int or rank <= 0 or rank > 50:
                raise ValueError("Prioridade da situação inválida")
            timestamp(record.get(time_field))
            verified[key] = {"rank": rank, time_field: record[time_field]}
        result[field] = verified
    return result


def read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de estado ausente: {path}")
    return validate_state(json.loads(path.read_text(encoding="utf-8")))


def write_state(path: Path, state: dict[str, Any]) -> None:
    payload = validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def plan_steps(state: dict[str, Any], episodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Ordena estados subsequentes; episódios repetidos na mesma cor não notificam."""
    state = validate_state(state)
    if not episodes:
        raise SourceError("Não é possível avançar sem episódios")
    if state["last_episode_at"] is None:
        latest = episodes[-1]
        if latest["state"] not in STATES:
            raise SourceError("Não é possível iniciar no estado legado; aguardar classificação atual")
        return [], {**state, "last_episode_at": latest["first_seen_at"], "last_level": latest["state"]}
    last_time = timestamp(state["last_episode_at"])
    matching = next((e for e in episodes if timestamp(e["first_seen_at"]) == last_time), None)
    if matching is None:
        raise SourceError("Cursor não localizado na memória do Observatório; revisar antes de reinicializar")
    if matching["state"] != state["last_level"]:
        raise SourceError("Nível persistido diverge do episódio referenciado; revisão necessária")
    steps: list[dict[str, Any]] = []
    current_level = state["last_level"]
    for episode in episodes:
        if timestamp(episode["first_seen_at"]) <= last_time:
            continue
        next_level = episode["state"]
        if next_level not in STATES:
            raise SourceError("Novo episódio usa estado legado; não é possível notificar sem classificação atual")
        steps.append({
            "episode": episode, "previous_level": current_level,
            "current_level": next_level, "notify": current_level != next_level,
            "next_state": {**state, "last_episode_at": episode["first_seen_at"],
                           "last_level": next_level},
        })
        current_level = next_level
    return steps, None


def remember_evidence(state: dict[str, Any], keys: list[str], at: str) -> dict[str, Any]:
    """Registra fundamentacoes confirmadas sem recriar notificacoes por recaptura.

    Uma evidência pode desaparecer temporariamente e voltar: o registro permanece.
    Prune só chaves antigas inativas para evitar crescimento ilimitado.
    """
    result = validate_state(state)
    now = timestamp(at)
    active = set(keys)
    seen = result["seen_evidence"]
    for key in active:
        if not re.fullmatch(r"[0-9a-f]{28}", key):
            raise ValueError("Assinatura de fundamentacao inválida")
    cutoff = now - timedelta(days=SEEN_RETENTION_DAYS)
    seen = {k: v for k, v in seen.items()
            if k in active or timestamp(v) >= cutoff}
    for key in sorted(active):
        seen.setdefault(key, at)
    if len(seen) > MAX_SEEN_EVIDENCE:
        inactive = sorted((k for k in seen if k not in active),
                          key=lambda k: timestamp(seen[k]))
        for key in inactive[:len(seen) - MAX_SEEN_EVIDENCE]:
            del seen[key]
    if len(seen) > MAX_SEEN_EVIDENCE:
        raise ValueError("Quantidade de fundamentacoes ativas ultrapassa limite de segurança")
    result["evidence_initialized"] = True
    result["seen_evidence"] = seen
    return result


def _expire_announcements(state: dict[str, Any], at: str) -> dict[str, Any]:
    """Retém a deduplicação por 45 dias; não elimina eventos ainda relevantes."""
    result = validate_state(state)
    cutoff = timestamp(at) - timedelta(days=SEEN_RETENTION_DAYS)
    result["announced_situations"] = {
        key: row for key, row in result["announced_situations"].items()
        if timestamp(row["notified_at"]) >= cutoff
    }
    return result


def prepare_situations(state: dict[str, Any], events: list, at: str) -> tuple[dict[str, Any], list]:
    """Atualiza fila e retorna novidades prontas para uma mensagem consolidada.

    Primeiro ciclo da nova versão apenas cria uma linha de base silenciosa.
    Nenhuma notícia antiga é reenviada durante a migração.
    """
    result = _expire_announcements(state, at)
    active = {event.key: event for event in events}
    if len(active) > MAX_MATERIAL_SITUATIONS:
        raise ValueError("Quantidade excessiva de situações atuais")
    if not result["material_initialized"]:
        result["material_initialized"] = True
        result["announced_situations"] = {
            **result["announced_situations"],
            **{k: {"rank": ev.rank, "notified_at": at} for k, ev in active.items()},
        }
        result["pending_situations"] = {}
        return validate_state(result), []

    announced = result["announced_situations"]
    pending = {}
    for key, event in active.items():
        if event.rank <= announced.get(key, {}).get("rank", 0):
            continue
        original = result["pending_situations"].get(key)
        pending[key] = {
            "rank": max(event.rank, original["rank"] if original else 0),
            "first_seen_at": original["first_seen_at"] if original else at,
        }
    if len(pending) > MAX_MATERIAL_SITUATIONS:
        raise ValueError("Quantidade excessiva de situações pendentes")
    result["pending_situations"] = pending
    current = timestamp(at)
    due = any(
        event.rank >= 30 or current - timestamp(pending[event.key]["first_seen_at"])
        >= timedelta(minutes=CONSOLIDATION_MINUTES)
        for event in active.values() if event.key in pending
    )
    ready = [active[key] for key in sorted(pending, key=lambda k: (active[k].day, k))] if due else []
    return validate_state(result), ready


def acknowledge_situations(state: dict[str, Any], events: list, at: str) -> dict[str, Any]:
    """Confirmar somente depois de o Telegram aceitar a mensagem."""
    result = _expire_announcements(state, at)
    records = result["announced_situations"]
    pending = result["pending_situations"]
    for event in events:
        current = records.get(event.key)
        if current is None or event.rank > current["rank"]:
            records[event.key] = {"rank": event.rank, "notified_at": at}
        pending.pop(event.key, None)
    result["material_initialized"] = True
    return validate_state(result)


def reset_material_after_transition(state: dict[str, Any], events: list, at: str) -> dict[str, Any]:
    """Uma mudança de nível já comunica o quadro atual; não repetir em continuidade."""
    result = _expire_announcements(state, at)
    result["pending_situations"] = {}
    return acknowledge_situations(result, events, at)
