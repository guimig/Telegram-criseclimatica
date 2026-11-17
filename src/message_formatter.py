"""Mensagens concisas, rastreáveis e sem confusão entre avisos e decisões."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .evidence import Driver, extract_drivers
from .source import SourceError, timestamp

LABELS = {
    "verde": ("🟢", "Normalidade"), "azul": ("🔵", "Preparação"),
    "amarelo": ("🟡", "Atenção"), "laranja": ("🟠", "Alerta"),
    "vermelho": ("🔴", "Alerta máximo"), "roxo": ("🟣", "Calamidade"),
    "partial": ("⚪", "Sem classificação"),
}
ORDER = {"verde": 0, "azul": 1, "amarelo": 2, "laranja": 3, "vermelho": 4, "roxo": 5}
PANEL = "https://guimig.github.io/criseclimatica/"
FOOTER = "Classificação consultiva automatizada. Consulte as fontes oficiais e a autoridade competente.\n🔗 " + PANEL
LOCAL = ZoneInfo("America/Sao_Paulo")
PERIODS = {"manha": "manhã", "tarde": "tarde", "noite": "noite", "dia": "dia"}


def clip(value: object, limit: int = 240) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def local_time(value: str) -> str:
    return timestamp(value).astimezone(LOCAL).strftime("%d/%m/%Y %H:%M")


def _period(day: str | None, period: str | None) -> str:
    if not day:
        return ""
    try:
        date = datetime.strptime(day, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return ""
    return f"{date}, {PERIODS.get(period, period)}" if period else date


def _driver_lines(driver: Driver) -> list[str]:
    source = clip(driver.source or "Fonte monitorada", 65)
    if driver.kind == "aviso":
        text = f"• {source} — {clip(driver.title or driver.reason or 'Novo aviso', 160)}"
        if driver.severity:
            text += f" ({clip(driver.severity, 45)})"
        lines = [text]
        if driver.scope and "regional" in driver.scope and "municipal" in driver.scope:
            lines.append("  Abrangência regional; inclusão municipal não confirmada.")
        period_verified = False
        if driver.start_at and driver.end_at:
            try:
                if timestamp(driver.start_at) < timestamp(driver.end_at):
                    lines.append(f"  Período informado: {local_time(driver.start_at)} a {local_time(driver.end_at)} (Brasília)")
                    period_verified = True
            except SourceError:
                pass
        if not period_verified:
            reported = clip(driver.reported_window, 130)
            lines.append("  Validade a confirmar." + (f" Fonte informa: {reported}" if reported else ""))
        if driver.url:
            lines.append(f"  Aviso completo: {driver.url}")
        else:
            lines.append("  Link individual não disponibilizado pela fonte; consulte o painel.")
        return lines
    if driver.kind == "previsão":
        phenomenon = ", ".join(driver.phenomena) or clip(driver.reason, 100)
        return [f"• {source} — previsão de {clip(phenomenon, 130)} ({_period(driver.day, driver.period)})"]
    if driver.kind == "publicação":
        return [f"• {source} — {clip(driver.reason or driver.title, 200)}"]
    return [f"• {source} — {clip(driver.reason, 185)}"]


def _finalize(lines: list[str]) -> str:
    """Preserva URLs completas e o rodapé; jamais corta a mensagem no meio."""
    footer = "\n\n" + FOOTER
    max_content = 3900 - len(footer) - len("\n(Outras evidências no painel.)")
    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line])
        if len(candidate) > max_content:
            kept.append("(Outras evidências no painel.)")
            break
        kept.append(line)
    message = "\n".join(kept).rstrip() + footer
    return message


def _append_drivers(lines: list[str], drivers: list[Driver]) -> None:
    if not drivers:
        return
    for d in drivers[:5]:
        lines.extend(_driver_lines(d))
    if len(drivers) > 5:
        lines.append(f"• Mais {len(drivers) - 5} fundamentação(ões) no painel.")


def format_transition(previous: str, current: str, at: str,
                      risk: dict | None, historical: bool = False,
                      drivers: list[Driver] | None = None) -> str:
    prev_icon, prev_name = LABELS[previous]
    icon, name = LABELS[current]
    if previous == "partial" or current == "partial":
        movement = "Mudança na disponibilidade da classificação"
    else:
        movement = ("Elevação do nível consultivo" if ORDER[current] > ORDER[previous]
                    else "Redução do nível consultivo")
    title = "ALTERAÇÃO HISTÓRICA DE NÍVEL" if historical else "MUDANÇA NO NÍVEL CONSULTIVO"
    lines = [
        f"{icon} {title} — ARAQUARI", "",
        f"Anterior: {prev_icon} {prev_name}", f"Atual: {icon} {name}",
        f"Movimento: {movement}", f"Coleta: {local_time(at)}",
    ]
    if historical:
        lines += ["", "Registro recuperado do histórico; não representa necessariamente a situação atual."]
    if risk and (risk.get("level") or "partial") == current:
        if drivers is None:
            drivers = extract_drivers(risk, at=risk.get("generated_at") or at)
        if drivers:
            lines += ["", "Fundamentação do nível:"]
            _append_drivers(lines, drivers)
        elif risk.get("reason"):
            lines += ["", "Motivo: " + clip(risk["reason"], 390)]
    else:
        lines += ["", "Detalhamento indisponível para esta transição histórica."]
    if current == "partial":
        lines += ["", "⚠️ Sem classificação não significa normalidade."]
    return _finalize(lines)


def format_continuity(level: str, at: str, new_drivers: list[Driver],
                      other_count: int = 0) -> str:
    """Informa novas razões sem afirmar elevação ou regressão inexistente."""
    if not new_drivers:
        raise ValueError("Não enviar continuidade sem nova fundamentação")
    icon, name = LABELS[level]
    lines = [
        f"{icon} CONTINUIDADE DO NÍVEL — ARAQUARI", "",
        f"Permanece: {icon} {name}",
        f"Nova fundamentação identificada: {local_time(at)}", "",
        "Novas evidências que sustentam a continuidade:",
    ]
    _append_drivers(lines, new_drivers)
    if other_count > 0:
        lines += ["", "Outros fundamentos do mesmo nível permanecem no painel."]
    return _finalize(lines)


def test_message() -> str:
    return ("🧪 TESTE — ROBÔ DO OBSERVATÓRIO\n\n"
            "Mensagem de teste. Não indica mudança de risco.\n" + PANEL)
