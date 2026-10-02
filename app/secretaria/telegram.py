import html
import logging
import re
import threading

import requests

from app.secretaria import config

log = logging.getLogger("secretaria.telegram")
LIMITE = 4000  # o Telegram aceita 4096 caracteres por mensagem

COMANDOS = [
    ("plano", "Monta (ou refaz) o plano de hoje"),
    ("adiantar", "O que fazer agora que adianta o futuro"),
    ("relatorio", "O que eu fiz (hoje, semana ou mes)"),
    ("tarefas", "Lista rápida de tudo que está aberto"),
    ("financas", "Saldos, faturas e contas do mês"),
    ("custo", "Quanto a IA custou no mês"),
    ("fechamento", "Fechar o dia agora"),
    ("ajuda", "Como falar comigo"),
]


def _api(metodo: str, **dados) -> dict:
    if not config.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN não configurado; %s ignorado", metodo)
        return {"ok": False, "description": "sem token"}
    try:
        resposta = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{metodo}",
            json=dados, timeout=20,
        )
        return resposta.json()
    except (requests.RequestException, ValueError) as erro:
        log.error("Telegram %s falhou: %s", metodo, erro)
        return {"ok": False, "description": str(erro)}


def _texto_puro(texto_html: str) -> str:
    return html.unescape(re.sub(r"</?(b|i|u|s|code|pre|a)(\s[^>]*)?>", "", texto_html))


def _partes(texto: str) -> list[str]:
    partes = []
    while len(texto) > LIMITE:
        corte = texto.rfind("\n", 0, LIMITE)
        if corte < LIMITE // 2:
            corte = LIMITE
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip("\n")
    if texto.strip():
        partes.append(texto)
    return partes


def enviar(chat_id, texto: str) -> bool:
    """Envia em HTML; se o Telegram recusar a formatação, manda como texto puro."""
    ok = True
    for parte in _partes(texto):
        r = _api("sendMessage", chat_id=chat_id, text=parte, parse_mode="HTML",
                 link_preview_options={"is_disabled": True})
        if not r.get("ok"):
            r = _api("sendMessage", chat_id=chat_id, text=_texto_puro(parte),
                     link_preview_options={"is_disabled": True})
        ok = ok and bool(r.get("ok"))
        if not r.get("ok"):
            log.error("Não consegui enviar mensagem: %s", r.get("description"))
    return ok


class Digitando:
    """Mostra "digitando..." enquanto a secretária pensa (renova a cada 4s)."""

    def __init__(self, chat_id):
        self.chat_id = chat_id
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._parar.is_set():
            _api("sendChatAction", chat_id=self.chat_id, action="typing")
            self._parar.wait(4)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._parar.set()


def configurar_webhook(url_base: str) -> dict:
    r = _api("setWebhook", url=f"{url_base}/secretaria/telegram",
             secret_token=config.TELEGRAM_WEBHOOK_SECRET,
             allowed_updates=["message"])
    _api("setMyCommands", commands=[{"command": c, "description": d} for c, d in COMANDOS])
    return r
