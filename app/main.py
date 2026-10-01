import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.routes import zap
from app.models import database
from app.secretaria import config as secretaria_config, rotas as secretaria, rotinas, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

database.create_tables()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("RENDER") and database.DATABASE_URL.startswith("sqlite"):
        logging.getLogger("secretaria").warning(
            "Usando SQLite no Render: as tarefas somem a cada deploy/reinício. Configure DATABASE_URL (Postgres).")
    if secretaria_config.TELEGRAM_BOT_TOKEN and secretaria_config.PUBLIC_URL:
        logging.getLogger("secretaria").info(
            "Webhook do Telegram: %s", telegram.configurar_webhook(secretaria_config.PUBLIC_URL))
    rotinas.iniciar_relogio()
    yield


app = FastAPI(lifespan=lifespan)

app.include_router(zap.router, prefix="/zap", tags=["Zap"])
app.include_router(secretaria.router, prefix="/secretaria", tags=["Secretária"])

@app.get("/")
def home():
    return {"msg": "API online Render"}
