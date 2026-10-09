import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from app.routes import zap
from app.models import database
from app.secretaria import banco, config as secretaria_config, diagnostico, rotas as secretaria, rotas_app, rotinas, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

database.create_tables()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not banco.configurado():
        logging.getLogger("secretaria").warning(
            "FIREBASE_CREDENCIAIS não configurado: a secretária não tem onde guardar tarefas e finanças.")
    if not secretaria_config.DONO_EMAIL:
        logging.getLogger("secretaria").warning("DONO_EMAIL não configurado: ninguém consegue usar o app.")
    if secretaria_config.TELEGRAM_BOT_TOKEN and secretaria_config.PUBLIC_URL:
        logging.getLogger("secretaria").info(
            "Webhook do Telegram: %s", telegram.configurar_webhook(secretaria_config.PUBLIC_URL))
    rotinas.iniciar_relogio()
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(zap.router, prefix="/zap", tags=["Zap"])
app.include_router(secretaria.router, prefix="/secretaria", tags=["Secretária"])
app.include_router(rotas_app.api, prefix="/secretaria/app", tags=["App"])
app.include_router(rotas_app.pwa, prefix="/app")


@app.middleware("http")
async def anotar_pedidos_do_app(request: Request, call_next):
    resposta = await call_next(request)
    if request.url.path.startswith("/app"):
        diagnostico.anotar(request.method, request.url.path, request.url.query, request.headers, resposta.status_code)
    return resposta

@app.get("/")
def home():
    return {"msg": "API online Render"}
