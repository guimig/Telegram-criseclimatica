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
                 "evidence_initialized": False, "seen_evidence": {}}
SEEN_RETENTION_DAYS = 45
MAX_SEEN_EVIDENCE = 500


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
