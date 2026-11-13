"""Decide se o agendamento GitHub deve executar como contingência do cron-job.org.

O histórico de episódios (bot-state) só avança quando a classificação muda. Por isso
não serve como heartbeat. Usamos a API do GitHub para observar execuções recentes
e BEM-SUCEDIDAS disparadas especificamente pela origem cron-job.org.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PRIMARY_TITLE = "Telegram risco | cron-job.org"
DEFAULT_MAX_AGE_MINUTES = 15
WORKFLOW_FILE = "monitor.yml"


def parse_github_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant.astimezone(timezone.utc) if instant.tzinfo is not None else None


def has_recent_primary_success(
    runs: list[dict], *, now: datetime, max_age_minutes: int = DEFAULT_MAX_AGE_MINUTES
) -> bool:
    """Apenas execução externa real e concluída pode suprimir o backup."""
    if now.tzinfo is None or max_age_minutes <= 0:
        raise ValueError("Relógio e limite de idade precisam ser válidos")
    current = now.astimezone(timezone.utc)
    for item in runs:
        if not isinstance(item, dict):
            continue
        if (
            item.get("event") != "workflow_dispatch"
            or item.get("head_branch") != "main"
            or item.get("display_title") != PRIMARY_TITLE
            or item.get("status") != "completed"
            or item.get("conclusion") != "success"
        ):
            continue
        finished_at = parse_github_timestamp(item.get("updated_at"))
        if finished_at is None:
            continue
        age = current - finished_at
        if -timedelta(minutes=2) <= age <= timedelta(minutes=max_age_minutes):
            return True
    return False


def get_workflow_runs(repository: str, token: str) -> list[dict]:
    """Consulta somente resultados da execução externa na própria conta GitHub."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("GITHUB_REPOSITORY inválido")
    if not token:
        raise ValueError("GITHUB_TOKEN ausente")
    query = urlencode(
        {"event": "workflow_dispatch", "branch": "main", "status": "success", "per_page": "50"}
    )
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?{query}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Telegram-criseclimatica-fallback/1.0",
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read(700_000))
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise ValueError("Resposta inválida da API de execuções")
    return payload["workflow_runs"]


def backup_needed(repository: str, token: str, *, now: datetime | None = None) -> bool:
    """Em caso de falha na consulta, executar a contingência, nunca ficar sem coleta."""
    now = now or datetime.now(timezone.utc)
    try:
        runs = get_workflow_runs(repository, token)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        # Não imprimir URL ou cabeçalhos: podem carregar referências autenticadas.
        print(f"[CONTINGÊNCIA] Não foi possível avaliar a API ({type(exc).__name__}). Executando backup.")
        return True
    if has_recent_primary_success(runs, now=now):
        print("[CONTINGÊNCIA] cron-job.org executou com sucesso recentemente; monitor GitHub dispensado.")
        return False
    print("[CONTINGÊNCIA] Sem sucesso confirmado do cron-job.org nos últimos 15 min; executar monitor.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Avalia execução de contingência do monitor Telegram")
    parser.add_argument("--github-output", type=Path, default=None)
    args = parser.parse_args()
    should_run = backup_needed(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", ""))
    result = "true" if should_run else "false"
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"should_run={result}\n")
    print(f"[CONTINGÊNCIA] should_run={result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
