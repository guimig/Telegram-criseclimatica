"""Monitor de níveis e novidades meteorológicas materiais, sem mensagens redundantes."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .evidence import extract_drivers
from .situations import aggregate_situations
from .message_formatter import format_transition, format_continuity, test_message
from .source import BASE_URL, SourceError, load_publication, load_snapshots
from .state import (
    plan_steps, read_state, write_state, prepare_situations,
    acknowledge_situations, reset_material_after_transition,
)
from .telegram_client import TelegramError, send_telegram
from .bordering import events_from_dashboard, plan_bordering, acknowledge_bordering, format_bordering


def process(state_path: Path, *, dry_run: bool = False, base_url: str = BASE_URL) -> int:
    publication = load_publication(base_url)
    state = read_state(state_path)
    episodes = publication.history["episodes"]
    steps, baseline = plan_steps(state, episodes)
    current_risk = publication.dashboard["risk_classification"]
    events = aggregate_situations(
        extract_drivers(current_risk, at=publication.publication_at)
    )
    bordering_events = events_from_dashboard(publication.dashboard, publication.publication_at)
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    def persist() -> None:
        if not dry_run:
            write_state(state_path, state)

    def transmit(message: str) -> None:
        if dry_run:
            print("[SIMULAÇÃO]", message.replace("\n", " | "))
            return
        if not token or not chat_id:
            raise TelegramError("Configure TELEGRAM_TOKEN e TELEGRAM_CHAT_ID nos GitHub Secrets")
        message_id = send_telegram(token, chat_id, message)
        print("[ENVIADO] Telegram confirmou message_id=", message_id)

    if baseline:
        state = reset_material_after_transition(baseline, events, publication.publication_at)
        if bordering_events is not None:
            state, _ = plan_bordering(state, bordering_events, publication.publication_at)
        persist()
        print("[BASELINE] Nível inicial e situações materiais registrados sem envio.")
        return 0

    snapshots = {}
    if any(step["notify"] for step in steps):
        try:
            snapshots = load_snapshots(base_url, publication.publication_at)
        except SourceError as exc:
            print("[AVISO] Detalhes de transições anteriores indisponíveis:", exc, file=sys.stderr)

    sent = 0
    current_transition_sent = False
    for step in steps:
        is_current = step["episode"]["first_seen_at"] == episodes[-1]["first_seen_at"]
        if step["notify"]:
            at = step["episode"]["first_seen_at"]
            risk = current_risk if is_current else snapshots.get(at)
            text = format_transition(
                step["previous_level"], step["current_level"], at, risk,
                historical=not is_current, events=events if is_current else None
            )
            transmit(text)
            sent += 1

        # O cursor de nível avança apenas após confirmação de cada notificação.
        state = {**state, "last_episode_at": step["next_state"]["last_episode_at"],
                 "last_level": step["next_state"]["last_level"]}
        if is_current and step["notify"]:
            # O aviso de mudança já informou a situação, não repetir em continuidade.
            state = reset_material_after_transition(state, events, publication.publication_at)
            current_transition_sent = True
        persist()

    if not current_transition_sent:
        migrating = not state["material_initialized"]
        state, ready = prepare_situations(state, events, publication.publication_at)
        # Guardar a fila e a hora do primeiro registro ANTES de tentar enviar.
        persist()
        if migrating:
            print("[MIGRAÇÃO] Cenários já conhecidos consolidados sem reenvio.")
        elif ready:
            message = format_continuity(
                current_risk["level"], publication.publication_at, ready)
            transmit(message)
            state = acknowledge_situations(state, ready, publication.publication_at)
            persist()
            print(f"[CONTINUIDADE] {len(ready)} situação(ões) materialmente nova(s).")
            sent += 1
        elif state["pending_situations"]:
            print(f"[CONSOLIDAÇÃO] {len(state['pending_situations'])} situação(ões) aguardando janela.")
        elif not steps:
            print("[SEM ALTERAÇÃO] Nenhuma mudança relevante para comunicar.")
    if bordering_events is not None:
        was_initialized = state["bordering_initialized"]
        state, bordering_pending = plan_bordering(state, bordering_events, publication.publication_at)
        persist()
        if not was_initialized:
            print("[LIMÍTROFES] Avisos existentes registrados sem envio inicial.")
        for event in bordering_pending:
            transmit(format_bordering(event, publication.publication_at, current_risk["level"]))
            state = acknowledge_bordering(state, event, publication.publication_at)
            persist()
            sent += 1
        if bordering_pending:
            print(f"[LIMÍTROFES] {len(bordering_pending)} novidade(s) oficial(is) comunicada(s).")
    return sent


def main() -> int:
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
