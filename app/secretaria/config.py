import hashlib
import os
from datetime import time
from zoneinfo import ZoneInfo


def _hora(valor: str, padrao: str) -> time:
    try:
        h, m = (valor or padrao).split(":")
        return time(int(h), int(m))
    except ValueError:
        h, m = padrao.split(":")
        return time(int(h), int(m))


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# ID do seu chat no Telegram. Só esse chat é atendido; mande /start pro bot
# sem isso configurado e ele responde com o seu ID.
TELEGRAM_OWNER_ID = os.getenv("TELEGRAM_OWNER_ID", "").strip()
# Segredo que o Telegram manda em todo webhook. Se não for definido, é derivado
# do token do bot, então não precisa configurar nada.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET") or (
    hashlib.sha256(f"secretaria:{TELEGRAM_BOT_TOKEN}".encode()).hexdigest()[:48]
    if TELEGRAM_BOT_TOKEN else ""
)

# Se definido, /secretaria/tick exige ?chave=<CRON_SECRET>.
CRON_SECRET = os.getenv("CRON_SECRET", "")

# O Render define RENDER_EXTERNAL_URL sozinho.
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")

# Dia a dia (anotar gasto, tarefa, responder) no modelo barato; plano do dia,
# fechamento, /adiantar e relatório no modelo que raciocina melhor.
MODELO = os.getenv("SECRETARIA_MODELO", "claude-haiku-4-5")
MODELO_PLANEJAMENTO = os.getenv("SECRETARIA_MODELO_PLANEJAMENTO", "claude-sonnet-5-5")
ESFORCO_CONVERSA = os.getenv("SECRETARIA_ESFORCO_CONVERSA", "low")  # ignorado no Haiku
ESFORCO_PLANEJAMENTO = os.getenv("SECRETARIA_ESFORCO_PLANEJAMENTO", "medium")
COTACAO_DOLAR = float(os.getenv("SECRETARIA_COTACAO_DOLAR", "5.5"))  # só para mostrar o custo em reais

FUSO = ZoneInfo(os.getenv("SECRETARIA_FUSO", "America/Sao_Paulo"))
HORA_PLANO = _hora(os.getenv("SECRETARIA_HORA_PLANO", ""), "06:00")
HORA_FECHAMENTO = _hora(os.getenv("SECRETARIA_HORA_FECHAMENTO", ""), "20:00")

# Espera alguns segundos antes de responder, pra juntar mensagens mandadas em
# sequência ("fiz X" / "e Y também") numa resposta só, como uma pessoa faria.
ESPERA_AGRUPAR_SEG = float(os.getenv("SECRETARIA_ESPERA_AGRUPAR_SEG", "4"))
# Quantas mensagens recentes ela relê pra entender "isso", "a segunda" etc.
HISTORICO_MENSAGENS = int(os.getenv("SECRETARIA_HISTORICO_MENSAGENS", "12"))
