from fastapi import APIRouter, Request
from sqlalchemy import select
from app.models.produto import produtos
from app.models.database import SessionLocal
import requests

router = APIRouter()

# 🔐 Dados da sua instância Z-API
ZAPI_INSTANCE = "3DFEBC76D35C60755AF8FA8592F99CB9"
ZAPI_TOKEN = "108648BD703ADBBBE798F920"
ZAPI_URL = f"https://api.z-api.io/instances/{ZAPI_INSTANCE}/token/{ZAPI_TOKEN}"

def enviar_whatsapp(numero: str, mensagem: str):
    payload = {
        "phone": numero,
        "message": mensagem
    }

    print("📤 Enviando mensagem para", numero)
    print("📦 Payload:", payload)

    response = requests.post(
        f"{ZAPI_URL}/send-message",
        json=payload
    )

    print("📨 Resposta Z-API:", response.status_code, "-", response.text)

@router.post("/zap/webhook")
async def receber_msg(request: Request):
    dados = await request.json()

    print("🔥 JSON recebido:", dados)

    numero = dados.get("phone", "")
    msg = dados.get("text", {}).get("message", "").lower()

    print("✅ Vai enviar resposta automática...")

    db = SessionLocal()
    resposta = "Olá! Digite o nome de um produto para consultar ou 'pix' para pagar."

    if "pix" in msg:
        resposta = "Aqui está o link de pagamento: https://pix.seusite.com/link123"
    else:
        produtos_lista = db.execute(select(produtos)).fetchall()
        for p in produtos_lista:
            p_mapping = dict(p._mapping)
            if p_mapping["nome"].lower() in msg:
                resposta = f"{p_mapping['nome']} custa R$ {p_mapping['preco']:.2f}"
                break

    enviar_whatsapp(numero, resposta)
    return {"status": "ok"}
