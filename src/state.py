"""Progresso durável por episódio amostrado, independente do GitHub Actions cache."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .source import STATES, SourceError, timestamp

DEFAULT_STATE = {"schema_version": 1, "location_id": "araquari",
                 "last_episode_at": None, "last_level": None}


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
    return {key: state[key] for key in DEFAULT_STATE}


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
        return [], {**state, "last_episode_at": latest["first_seen_at"], "last_level": latest["state"]}
    last_time = timestamp(state["last_episode_at"])
    if not any(timestamp(e["first_seen_at"]) == last_time for e in episodes):
        raise SourceError("Cursor não localizado na memória do Observatório; revisar antes de reinicializar")
    steps: list[dict[str, Any]] = []
    current_level = state["last_level"]
    for episode in episodes:
        if timestamp(episode["first_seen_at"]) <= last_time:
            continue
        next_level = episode["state"]
        steps.append({
            "episode": episode, "previous_level": current_level,
            "current_level": next_level, "notify": current_level != next_level,
            "next_state": {**state, "last_episode_at": episode["first_seen_at"],
                           "last_level": next_level},
        })
        current_level = next_level
    return steps, None
