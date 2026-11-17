"""Monitor de níveis consultivos e novas fundamentações materiais."""
import argparse
import os
import sys
from pathlib import Path

from .evidence import extract_drivers
from .message_formatter import format_transition, format_continuity, test_message
from .source import BASE_URL, SourceError, load_publication, load_snapshots
from .state import plan_steps, read_state, remember_evidence, write_state
from .telegram_client import TelegramError, send_telegram


def process(state_path, *, dry_run=False, base_url=BASE_URL):
    publication = load_publication(base_url)
    state = read_state(state_path)
    episodes = publication.history["episodes"]
    steps, baseline = plan_steps(state, episodes)
    current_risk = publication.dashboard["risk_classification"]
    current_drivers = extract_drivers(current_risk, at=publication.publication_at)
    keys = [d.key for d in current_drivers]
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if baseline:
        # A primeira execução nunca inventa transição ou motivo novo.
        initialized = remember_evidence(baseline, keys, publication.publication_at)
        print("[BASELINE] Nível observado:", initialized["last_level"],
              "fundamentações:", len(keys))
        if not dry_run:
            write_state(state_path, initialized)
        return 0

    notices = [step for step in steps if step["notify"]]
    snapshots = {}
    if notices:
        try:
            snapshots = load_snapshots(base_url, publication.publication_at)
        except SourceError as exc:
            print("[AVISO] Detalhes históricos indisponíveis:", exc, file=sys.stderr)

    # Migração silenciosa: o cursor anterior só acompanhava níveis. Não é
    # possível saber retrospectivamente quais motivos foram notificados.
    upgrading = not state["evidence_initialized"]
    pending_drivers = [d for d in current_drivers
                       if d.key not in state["seen_evidence"]] if not upgrading else []
    current_will_transition = any(s["notify"] and
                                  s["episode"]["first_seen_at"] == episodes[-1]["first_seen_at"]
                                  for s in steps)
    continuity_needed = bool(pending_drivers) and not current_will_transition

    if (notices or continuity_needed) and not dry_run and (not token or not chat_id):
        raise TelegramError("Configure TELEGRAM_TOKEN e TELEGRAM_CHAT_ID nos GitHub Secrets")

    sent = 0
    for step in steps:
        at = step["episode"]["first_seen_at"]
        current = at == episodes[-1]["first_seen_at"]
        if step["notify"]:
            risk = current_risk if current else snapshots.get(at)
            details = current_drivers if current else None
            message = format_transition(step["previous_level"], step["current_level"], at,
                                        risk, historical=not current, drivers=details)
            if dry_run:
                print("[SIMULAÇÃO] ALTERAÇÃO:", message.replace("\n", " | "))
            else:
                message_id = send_telegram(token, chat_id, message)
                print("[ENVIADO] Mudança de nível:", at, step["current_level"],
                      "message_id=", message_id)
            sent += 1
        state = step["next_state"]
        # A mensagem de transição atual já comunica as causas; não mandar
        # segunda mensagem de continuidade no mesmo ciclo.
        if current:
            state = remember_evidence(state, keys, publication.publication_at)
        if not dry_run:
            write_state(state_path, state)

    if not steps:
        if upgrading:
            state = remember_evidence(state, keys, publication.publication_at)
            print("[MIGRAÇÃO] Fundamentações atuais registradas sem reenvio:", len(keys))
            if not dry_run:
                write_state(state_path, state)
        elif pending_drivers:
            old_count = len(current_drivers) - len(pending_drivers)
            message = format_continuity(
                current_risk["level"], publication.publication_at, pending_drivers,
                other_count=old_count)
            if dry_run:
                print("[SIMULAÇÃO] CONTINUIDADE:", message.replace("\n", " | "))
            else:
                message_id = send_telegram(token, chat_id, message)
                print("[ENVIADO] Nova fundamentação com nível mantido:",
                      publication.publication_at, "novas=", len(pending_drivers),
                      "message_id=", message_id)
            sent += 1
            state = remember_evidence(state, keys, publication.publication_at)
            if not dry_run:
                write_state(state_path, state)
        else:
            print("[SEM ALTERAÇÃO] Nenhum nível ou fundamento novo.")
    else:
        # Se os episódios registrados terminaram sem mudança efetiva de nível
        # (p.ex., nova versão de regra), ainda podem existir causas novas.
        if not current_will_transition and upgrading:
            state = remember_evidence(state, keys, publication.publication_at)
            if not dry_run:
                write_state(state_path, state)
        elif not current_will_transition and pending_drivers:
            message = format_continuity(
                current_risk["level"], publication.publication_at, pending_drivers,
                other_count=len(current_drivers) - len(pending_drivers))
            if dry_run:
                print("[SIMULAÇÃO] CONTINUIDADE:", message.replace("\n", " | "))
            else:
                message_id = send_telegram(token, chat_id, message)
                print("[ENVIADO] Nova fundamentação com nível mantido:",
                      publication.publication_at, "novas=", len(pending_drivers),
                      "message_id=", message_id)
            sent += 1
            state = remember_evidence(state, keys, publication.publication_at)
            if not dry_run:
                write_state(state_path, state)
    return sent


def main():
    parser = argparse.ArgumentParser(description="Monitor Telegram de risco consultivo")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-message", action="store_true")
    parser.add_argument("--state", type=Path, default=Path(os.getenv("BOT_STATE_PATH", "state.json")))
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    try:
        if args.test_message:
            if args.dry_run:
                print(test_message())
            else:
                print("[TESTE] message_id=", send_telegram(os.getenv("TELEGRAM_TOKEN", ""),
                      os.getenv("TELEGRAM_CHAT_ID", ""), test_message()))
            return 0
        print("[OK] Notificações:", process(args.state, dry_run=args.dry_run, base_url=args.base_url))
        return 0
    except (SourceError, ValueError, OSError, TelegramError) as exc:
        print("[ERRO]", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
