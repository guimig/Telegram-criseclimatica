"""Notificações da classificação consultiva do Observatório."""
from zoneinfo import ZoneInfo
from .source import timestamp

LABELS = {
    "verde": ("🟢", "Normalidade"), "azul": ("🔵", "Preparação"),
    "amarelo": ("🟡", "Atenção"), "laranja": ("🟠", "Alerta"),
    "vermelho": ("🔴", "Alerta máximo"), "roxo": ("🟣", "Calamidade"),
    "partial": ("⚪", "Sem classificação"),
}
ORDER = {"verde": 0, "azul": 1, "amarelo": 2, "laranja": 3, "vermelho": 4, "roxo": 5}
PANEL = "https://guimig.github.io/criseclimatica/"

def clip(value, size=360):
    text = " ".join(str(value or "").split())
    return text if len(text) <= size else text[:size - 1] + "…"

def format_transition(previous, current, at, risk, historical=False):
    old_icon, old_name = LABELS[previous]
    icon, name = LABELS[current]
    moment = timestamp(at).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M")
    if current == "partial" or previous == "partial":
        direction = "Mudança na disponibilidade da classificação"
    else:
        direction = "Elevação do risco consultivo" if ORDER[current] > ORDER[previous] else "Redução do risco consultivo"
    title = "TRANSIÇÃO HISTÓRICA" if historical else "MUDANÇA NO NÍVEL DE RISCO"
    lines = [f"{icon} {title} — ARAQUARI", "",
             f"Anterior: {old_icon} {old_name}", f"Atual: {icon} {name}",
             f"Movimento: {direction}", f"Coleta: {moment}"]
    if historical:
        lines += ["", "ℹ️ Registro histórico recuperado. Não representa necessariamente o risco atual."]
    if isinstance(risk, dict) and (risk.get("level") or "partial") == current:
        lines += ["", "Motivo: " + clip(risk.get("reason") or "Não informado.", 600)]
        if risk.get("valid_until"):
            expiry = timestamp(risk["valid_until"]).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M")
            lines.append("Validade da evidência: " + expiry)
        evidence = next((c.get("evidence", []) for c in risk.get("candidates", [])
                         if isinstance(c, dict) and c.get("level") == risk.get("level")), [])
        details = []
        for item in evidence[:3]:
            if not isinstance(item, dict):
                continue
            source = clip(item.get("source") or item.get("station"), 65)
            info = clip(item.get("title") or ", ".join(item.get("phenomena") or []), 110)
            if not info and isinstance(item.get("value"), (int, float)) and item.get("unit"):
                info = str(item["value"]) + " " + clip(item["unit"], 20)
            if source or info:
                details.append("• " + clip(": ".join(t for t in (source, info) if t), 200))
            url = item.get("url")
            if isinstance(url, str) and url.startswith("https://") and len(url) < 250:
                details.append("  Fonte: " + url)
            start = item.get("start_at")
            end = item.get("end_at")
            if start and end:
                try:
                    first = timestamp(start).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")
                    last = timestamp(end).astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")
                    if timestamp(start) < timestamp(end):
                        details.append("  Período informado: " + first + " a " + last)
                except Exception:
                    # Períodos não conciliados não são apresentados como horários confirmados.
                    pass
        if details:
            lines += ["", "Evidências:", *details]
    else:
        lines += ["", "Motivo detalhado indisponível para este episódio."]
    if current == "partial":
        lines += ["", "⚠️ Insuficiência de dados não equivale à normalidade."]
    lines += ["", "Classificação consultiva automatizada; não é alerta oficial da Defesa Civil nem decisão institucional sobre atividades.",
              "Consulte as fontes oficiais e a autoridade competente.", "🔗 " + PANEL]
    message = "\n".join(lines)
    return message[:3900]

def test_message():
    return "🧪 TESTE — ROBÔ DO OBSERVATÓRIO\n\nMensagem de teste. Não indica mudança de risco.\n" + PANEL
