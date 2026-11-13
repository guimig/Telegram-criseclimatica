"""Cliente Telegram Bot API usando apenas a biblioteca padrão do Python."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    pass


def send_telegram(token: str, chat_id: str, message: str) -> int:
    """Retorna ID da mensagem após confirmação da API; não registra o token."""
    if not token or not chat_id:
        raise TelegramError("TELEGRAM_TOKEN e TELEGRAM_CHAT_ID precisam estar configurados")
    if not message or len(message) > 3900:
        raise TelegramError("Mensagem vazia ou longa demais para envio seguro")
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=urlencode({"chat_id": chat_id, "text": message,
                        "disable_web_page_preview": "true"}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(20_000)
    except HTTPError as exc:
        # O URL da API contém o token. Não incluir repr(exc), request ou URL no log.
        raise TelegramError(f"Telegram retornou HTTP {exc.code}; conferir permissão do grupo e credenciais") from None
    except (URLError, TimeoutError, OSError):
        raise TelegramError("Falha de conexão com o Telegram; envio não confirmado") from None
    try:
        payload = json.loads(body)
    except ValueError:
        raise TelegramError("Resposta JSON inválida do Telegram") from None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise TelegramError("Telegram recusou a mensagem (ok=false)")
    result = payload.get("result") or {}
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise TelegramError("Telegram não confirmou o ID da mensagem")
    return result["message_id"]
