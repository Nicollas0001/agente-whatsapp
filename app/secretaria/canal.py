"""Por onde a secretária fala com a pessoa: o app (histórico + notificação) e, se configurado, o Telegram."""
import logging

from app.secretaria import config, push, telegram, repositorio as repo

log = logging.getLogger("secretaria.canal")


def telegram_ativo() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_OWNER_ID)


def tem_destino() -> bool:
    return telegram_ativo() or bool(push.inscricoes())


def entregar(texto: str, notificar: bool = True) -> int:
    """Guarda no histórico (o app mostra) e avisa no tablet; manda no Telegram se estiver ligado."""
    seq = repo.salvar_mensagem("assistant", texto)
    if notificar:
        try:
            push.enviar("Secretária", telegram.texto_puro(texto))
        except Exception:
            log.exception("Não consegui mandar a notificação")
    if telegram_ativo():
        telegram.enviar(config.TELEGRAM_OWNER_ID, texto)
    return seq
