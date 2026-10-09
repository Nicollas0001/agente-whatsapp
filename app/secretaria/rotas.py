import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.secretaria import banco, config, ia, rotinas, telegram, repositorio as repo

log = logging.getLogger("secretaria")
router = APIRouter()


@router.post("/telegram")
def receber_telegram(update: dict, tarefas_fundo: BackgroundTasks,
                     x_telegram_bot_api_secret_token: str = Header(default="")):
    segredo = x_telegram_bot_api_secret_token
    if not config.TELEGRAM_WEBHOOK_SECRET or not hmac.compare_digest(segredo, config.TELEGRAM_WEBHOOK_SECRET):
        raise HTTPException(status_code=403)

    mensagem = update.get("message") or {}
    chat_id = (mensagem.get("chat") or {}).get("id")
    if chat_id is None:
        return {"ok": True}

    if not config.TELEGRAM_OWNER_ID:
        telegram.enviar(chat_id, f"Oi! Seu ID do Telegram é <code>{chat_id}</code>.\n"
                                 "Coloque <code>TELEGRAM_OWNER_ID</code> com esse número nas variáveis "
                                 "do servidor e me mande /start de novo.")
        return {"ok": True}
    if str(chat_id) != config.TELEGRAM_OWNER_ID:
        log.warning("Mensagem ignorada de chat desconhecido %s", chat_id)
        return {"ok": True}

    # O Telegram reenvia o mesmo update se a resposta demorar; ignora repetidos.
    update_id = int(update.get("update_id", 0))
    if update_id and update_id <= int(repo.estado_get("telegram_update_id") or 0):
        return {"ok": True}
    if update_id:
        repo.estado_set("telegram_update_id", str(update_id))

    texto = (mensagem.get("text") or mensagem.get("caption") or "").strip()
    if not texto:
        telegram.enviar(chat_id, "Por enquanto eu só entendo texto. Me escreve o que era?")
        return {"ok": True}
    mensagem_id = repo.salvar_mensagem("user", texto, processada=False)
    if not repo.estado_get("iniciado"):
        repo.estado_set("iniciado", repo.agora().isoformat())

    tarefas_fundo.add_task(rotinas.ao_receber, mensagem_id)
    return {"ok": True}


@router.api_route("/tick", methods=["GET", "POST"])
def tick(chave: str = ""):
    """Chamado por um cron externo (ex.: cron-job.org a cada 10 min).
    Também mantém o servidor acordado no plano grátis do Render."""
    if config.CRON_SECRET and not hmac.compare_digest(chave, config.CRON_SECRET):
        raise HTTPException(status_code=403)
    return rotinas.tick()


@router.get("/status")
def status():
    """Diagnóstico rápido da configuração (não mostra segredos)."""
    return {
        "telegram_token": bool(config.TELEGRAM_BOT_TOKEN),
        "dono_configurado": bool(config.TELEGRAM_OWNER_ID),
        "anthropic_key": bool(ia.chave()),
        "firebase": banco.configurado(),
        "dono_email": bool(config.DONO_EMAIL),
        "url_publica": config.PUBLIC_URL or None,
        "modelo_dia_a_dia": config.MODELO,
        "modelo_planejamento": config.MODELO_PLANEJAMENTO,
        "hora_plano": config.HORA_PLANO.strftime("%H:%M"),
        "hora_fechamento": config.HORA_FECHAMENTO.strftime("%H:%M"),
        "agora": repo.agora().isoformat(sep=" "),
        "no_ar_desde": rotinas.NO_AR_DESDE.isoformat(sep=" ", timespec="seconds"),
        "manter_acordado_min": config.MANTER_ACORDADO_MIN,
    }
