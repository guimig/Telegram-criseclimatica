"""Processador independente das mudanças do risco consultivo."""
import argparse
import os
import sys
from pathlib import Path
from .message_formatter import format_transition, test_message
from .source import BASE_URL, SourceError, load_publication, load_snapshots
from .state import plan_steps, read_state, write_state
from .telegram_client import TelegramError, send_telegram

def process(state_path, *, dry_run=False, base_url=BASE_URL):
    publication = load_publication(base_url)
    steps, baseline = plan_steps(read_state(state_path), publication.history["episodes"])
    if baseline:
        print("[BASELINE] Estado inicial:", baseline["last_level"], baseline["last_episode_at"])
        if not dry_run:
            write_state(state_path, baseline)
        return 0
    notices = [step for step in steps if step["notify"]]
    snapshots = {}
    if notices:
        try:
            snapshots = load_snapshots(base_url, publication.publication_at)
        except SourceError as exc:
            print("[AVISO] Evidência histórica complementar indisponível:", exc, file=sys.stderr)
    token, chat_id = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if notices and not dry_run and (not token or not chat_id):
        raise TelegramError("Configure TELEGRAM_TOKEN e TELEGRAM_CHAT_ID nos GitHub Secrets")
    episodes = publication.history["episodes"]
    for step in steps:
        if step["notify"]:
            at = step["episode"]["first_seen_at"]
            current = at == episodes[-1]["first_seen_at"]
            risk = publication.dashboard["risk_classification"] if current else snapshots.get(at)
            message = format_transition(step["previous_level"], step["current_level"], at, risk, historical=not current)
            if dry_run:
                print("[SIMULAÇÃO]", message.replace("\n", " | "))
            else:
                message_id = send_telegram(token, chat_id, message)
                print("[ENVIADO]", at, step["current_level"], "message_id=", message_id)
        if not dry_run:
            write_state(state_path, step["next_state"])
    if not steps:
        print("[SEM ALTERAÇÃO] Nenhuma transição nova.")
    return len(notices)

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
