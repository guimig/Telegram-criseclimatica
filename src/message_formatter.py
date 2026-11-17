"""Mensagens acessíveis: síntese da situação e um único link para o Observatório."""
from __future__ import annotations

from zoneinfo import ZoneInfo

from .evidence import Driver, extract_drivers
from .situations import Situation, aggregate_situations
from .source import timestamp

LABELS = {
    "verde": ("🟢", "Normalidade"), "azul": ("🔵", "Preparação"),
    "amarelo": ("🟡", "Atenção"), "laranja": ("🟠", "Alerta"),
    "vermelho": ("🔴", "Alerta máximo"), "roxo": ("🟣", "Calamidade"),
    "partial": ("⚪", "Sem classificação"),
}
ORDER = {"verde": 0, "azul": 1, "amarelo": 2, "laranja": 3, "vermelho": 4, "roxo": 5}
PANEL = "https://guimig.github.io/criseclimatica/"
FOOTER = (
    "Classificação consultiva automatizada. Consulte as fontes oficiais e a autoridade competente."
    "\n🔗 " + PANEL
)
LOCAL = ZoneInfo("America/Sao_Paulo")
MAX_EVENTS_IN_MESSAGE = 4


def clip(value: object, limit: int = 310) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def local_time(value: str) -> str:
    return timestamp(value).astimezone(LOCAL).strftime("%d/%m/%Y às %H:%M")


def _compose(lines: list[str]) -> str:
    """Preserva o rodapé e impede cortes no meio de um link ou informação."""
    footer = "\n\n" + FOOTER
    limit = 3900 - len(footer)
    result = []
    for line in lines:
        candidate = "\n".join((*result, line))
        if len(candidate) > limit:
            result.append("Consulte as demais informações no Observatório.")
            break
        result.append(line)
    return "\n".join(result).rstrip() + footer


def _situations(events: list[Situation]) -> list[str]:
    if not events:
        return []
    lines = [f"• {clip(event.summary)}" for event in events[:MAX_EVENTS_IN_MESSAGE]]
    if len(events) > MAX_EVENTS_IN_MESSAGE:
        lines.append(f"Mais {len(events) - MAX_EVENTS_IN_MESSAGE} situação(ões) no Observatório.")
    return lines


def format_transition(previous: str, current: str, at: str, risk: dict | None,
                      historical: bool = False, drivers: list[Driver] | None = None,
                      events: list[Situation] | None = None) -> str:
    before = LABELS[previous]
    after = LABELS[current]
    lines = [
        f"{after[0]} ARAQUARI — {after[1].upper()}",
        "",
        f"Nível consultivo: {before[0]} {before[1]} → {after[0]} {after[1]}",
        f"Atualização: {local_time(at)}",
    ]
    if historical:
        lines += ["", "Registro histórico recuperado; não descreve necessariamente a condição atual."]
    if risk and (risk.get("level") or "partial") == current:
        if events is None:
            relevant = drivers if drivers is not None else extract_drivers(
                risk, at=risk.get("generated_at") or at)
            events = aggregate_situations(relevant)
        if events:
            lines.extend(["", "Situação monitorada:", *_situations(events)])
        elif risk.get("reason"):
            lines.extend(["", "Motivo: " + clip(risk.get("reason"), 280)])
    elif historical:
        lines.extend(["", "Detalhes históricos indisponíveis; confira a situação atual no Observatório."])
    if current == "partial":
        lines.extend(["", "⚠️ Falta de dados para classificação não significa normalidade."])
    return _compose(lines)


def format_continuity(level: str, at: str, new_events: list[Situation],
                      other_count: int = 0) -> str:
    """Atualização material sem confundir continuidade com nova cor."""
    if not new_events:
        raise ValueError("Continuidade exige situação material nova")
    icon, name = LABELS[level]
    lines = [
        f"{icon} ARAQUARI — {name.upper()} MANTIDA",
        "",
        "Novas informações relevantes para o período monitorado:",
        *_situations(new_events),
        "",
        f"Nível consultivo permanece em {name}.",
        f"Atualização: {local_time(at)}",
    ]
    return _compose(lines)


def test_message() -> str:
    return (
        "🧪 TESTE — ROBÔ DO OBSERVATÓRIO\n\n"
        "Mensagem de teste. Não representa uma mudança de risco.\n"
        + PANEL
    )
