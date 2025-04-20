from fastapi import APIRouter, Request
from sqlalchemy import select
from app.models.produto import produtos
from app.models.database import SessionLocal
import requests

router = APIRouter()

ZAPI_INSTANCE = "sua_instancia"
ZAPI_TOKEN = "seu_token"
ZAPI_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"

def enviar_whatsapp(numero: str, mensagem: str):
    requests.post(f"{ZAPI_URL}/send-messages", json={{"phone": numero, "message": mensagem}})

@router.post("/webhook")
async def receber_msg(request: Request):
    dados = await request.json()
    msg = dados.get("message", "").lower()
    numero = dados.get("phone", "")
    db = SessionLocal()

    resposta = "Olá! Digite o nome de um produto para consultar ou 'pix' para pagar."

    if "pix" in msg:
        resposta = "Aqui está o link de pagamento: https://pix.seusite.com/link123"
    else:
        produtos_lista = db.execute(select(produtos)).fetchall()
        for p in produtos_lista:
            if p._mapping["nome"].lower() in msg:
                resposta = f"{p._mapping['nome']} custa R$ {p._mapping['preco']:.2f}"
                break

    enviar_whatsapp(numero, resposta)
    return {{"status": "ok"}}
